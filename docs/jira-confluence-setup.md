# Jira + Confluence Setup

Jibsa connects to Jira and Confluence using the same Atlassian credentials. Both use the team-shared API token — no per-user OAuth needed.

---

## 1. Create an Atlassian API Token

1. Go to [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**
3. Label it `Jibsa`
4. Copy the token

---

## 2. Add to `.env`

```
JIRA_SERVER=https://your-org.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=your-api-token-here
```

The same credentials work for both Jira and Confluence (Atlassian Cloud uses a single API token for all products).

---

## 3. Enable in `config/settings.yaml`

```yaml
integrations:
  jira:
    enabled: true
  confluence:
    enabled: true
```

Enable either or both — they're independent.

---

## 4. Assign to Interns

When hiring an intern or editing their JD, add `jira` and/or `confluence` to their tools:

```
@jibsa hire a project manager intern
# During the flow, specify tools_allowed: ["jira", "confluence", "notion"]
```

Or edit an existing intern:

```
@jibsa edit alex's jd
> add tool: jira
> add tool: confluence
> done
```

---

## 5. Available Actions

### Jira (read + write)

**Read** (during agent reasoning — no approval needed):
- Search issues via JQL: `"project = PROJ AND status = Open"`
- Get issue details by key: `"PROJ-123"`

**Write** (proposed as action plan — requires approval):
- `create_issue` — params: `project_key`, `summary`, `issue_type`, `description`, `priority`, `labels`
- `update_issue` — params: `issue_key`, `fields` (dict)
- `transition_issue` — params: `issue_key`, `transition_name` (e.g. "In Progress", "Done")
- `add_comment` — params: `issue_key`, `body`
- `add_worklog` — params: `issue_key`, `time_spent` (e.g. "2h"), `comment`

### Confluence (read + write)

**Read** (during agent reasoning):
- Search pages via CQL

**Write** (requires approval):
- `create_page` — params: `space_key`, `title`, `body` (HTML), `parent_id` (optional)
- `update_page` — params: `page_id`, `title`, `body` (HTML)
- `add_comment` — params: `page_id`, `body`

---

## Examples

```
@jibsa alex create a Jira ticket for the login bug in PROJ
@jibsa alex transition PROJ-42 to Done
@jibsa mia search Confluence for the deployment guide
@jibsa mia create a Confluence page in ENG space with today's retro notes
```

---

## Troubleshooting

**"Jira is not connected"**
- Check that `jira: enabled: true` is set in `settings.yaml`
- Verify `JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_API_TOKEN` are all set in `.env`
- Run `./scripts/doctor.sh` to check

**"Transition 'Done' not found"**
- Transition names are project-specific. The intern will show available transitions if the requested one doesn't exist.

**"Confluence search returned 0 results"**
- Make sure the API token has access to the Confluence space you're searching.
