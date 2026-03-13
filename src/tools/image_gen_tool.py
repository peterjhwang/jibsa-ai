"""
ImageGenTool — CrewAI tool for AI image generation via Nano Banana 2 (Gemini).

This is a write tool — the agent proposes an image generation action plan,
the user approves, and the orchestrator generates + uploads the image.
"""
import logging
import os
import tempfile
from pathlib import Path
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

VALID_ASPECT_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9"}
DEFAULT_ASPECT_RATIO = "1:1"


class ImageGenInput(BaseModel):
    """Input schema for image generation."""
    prompt: str = Field(..., description="Detailed description of the image to generate")
    aspect_ratio: str = Field(default=DEFAULT_ASPECT_RATIO, description="Aspect ratio: 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, or 16:9")
    filename: str = Field(default="generated_image.png", description="Output filename")


class ImageGenTool(BaseTool):
    name: str = "Generate Image"
    description: str = (
        "Generate an AI image using Nano Banana 2 (Google Gemini). "
        "This requires approval — the image will be proposed first, then generated after confirmation. "
        "Provide a detailed prompt describing the desired image. "
        "Supported aspect ratios: 1:1 (square), 9:16 (portrait), 16:9 (landscape), and more."
    )
    args_schema: Type[BaseModel] = ImageGenInput

    def _run(self, prompt: str, aspect_ratio: str = DEFAULT_ASPECT_RATIO, filename: str = "generated_image.png") -> str:
        if not os.environ.get("GOOGLE_API_KEY"):
            return "Image generation is not configured — GOOGLE_API_KEY is not set."

        if aspect_ratio not in VALID_ASPECT_RATIOS:
            return f"Invalid aspect ratio '{aspect_ratio}'. Valid: {', '.join(sorted(VALID_ASPECT_RATIOS))}"

        # Return instructions for the agent to propose an action plan
        short_prompt = prompt[:80] + "..." if len(prompt) > 80 else prompt
        return (
            f"To generate this image, propose an action plan with:\n"
            f'{{"type": "action_plan", "summary": "Generate image: {short_prompt}", '
            f'"steps": [{{"service": "image_gen", "action": "generate_image", '
            f'"params": {{"prompt": "<the full prompt>", "aspect_ratio": "{aspect_ratio}", "filename": "{filename}"}}, '
            f'"description": "Generate image: {short_prompt}"}}], '
            f'"needs_approval": true}}'
        )


def generate_and_save(prompt: str, aspect_ratio: str = DEFAULT_ASPECT_RATIO, filename: str = "generated_image.png") -> str:
    """Generate an image via Nano Banana 2 and save to temp file. Returns file path.

    Called by the orchestrator during plan execution (after approval).
    """
    from google import genai
    from google.genai import types

    client = genai.Client()  # reads GOOGLE_API_KEY from env

    response = client.models.generate_content(
        model="gemini-3.1-flash-image-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            aspect_ratio=aspect_ratio,
        ),
    )

    # Extract inline image data from response
    image_data = None
    for part in response.candidates[0].content.parts:
        if part.inline_data:
            image_data = part.inline_data.data
            break

    if not image_data:
        raise RuntimeError("Nano Banana 2 returned no image data")

    tmp_dir = tempfile.mkdtemp(prefix="jibsa_images_")
    file_path = Path(tmp_dir) / filename
    file_path.write_bytes(image_data)

    return str(file_path)
