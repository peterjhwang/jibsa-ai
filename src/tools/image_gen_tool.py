"""
ImageGenTool — CrewAI tool for AI image generation via DALL-E.

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

VALID_SIZES = {"1024x1024", "1024x1792", "1792x1024"}
DEFAULT_SIZE = "1024x1024"


class ImageGenInput(BaseModel):
    """Input schema for image generation."""
    prompt: str = Field(..., description="Detailed description of the image to generate")
    size: str = Field(default=DEFAULT_SIZE, description="Image size: 1024x1024, 1024x1792, or 1792x1024")
    filename: str = Field(default="generated_image.png", description="Output filename")


class ImageGenTool(BaseTool):
    name: str = "Generate Image"
    description: str = (
        "Generate an AI image using DALL-E. "
        "This requires approval — the image will be proposed first, then generated after confirmation. "
        "Provide a detailed prompt describing the desired image. "
        "Supported sizes: 1024x1024 (square), 1024x1792 (portrait), 1792x1024 (landscape)."
    )
    args_schema: Type[BaseModel] = ImageGenInput

    def _run(self, prompt: str, size: str = DEFAULT_SIZE, filename: str = "generated_image.png") -> str:
        if not os.environ.get("OPENAI_API_KEY"):
            return "Image generation is not configured — OPENAI_API_KEY is not set."

        if size not in VALID_SIZES:
            return f"Invalid size '{size}'. Valid: {', '.join(sorted(VALID_SIZES))}"

        # Return instructions for the agent to propose an action plan
        short_prompt = prompt[:80] + "..." if len(prompt) > 80 else prompt
        return (
            f"To generate this image, propose an action plan with:\n"
            f'{{"type": "action_plan", "summary": "Generate image: {short_prompt}", '
            f'"steps": [{{"service": "image_gen", "action": "generate_image", '
            f'"params": {{"prompt": "<the full prompt>", "size": "{size}", "filename": "{filename}"}}, '
            f'"description": "Generate image: {short_prompt}"}}], '
            f'"needs_approval": true}}'
        )


def generate_and_save(prompt: str, size: str = DEFAULT_SIZE, filename: str = "generated_image.png") -> str:
    """Generate an image via DALL-E and save to temp file. Returns file path.

    Called by the orchestrator during plan execution (after approval).
    """
    import httpx
    from openai import OpenAI

    client = OpenAI()  # uses OPENAI_API_KEY from env

    response = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size=size,
        n=1,
        response_format="url",
    )

    image_url = response.data[0].url

    # Download the image
    img_response = httpx.get(image_url)
    img_response.raise_for_status()

    tmp_dir = tempfile.mkdtemp(prefix="jibsa_images_")
    file_path = Path(tmp_dir) / filename
    file_path.write_bytes(img_response.content)

    return str(file_path)
