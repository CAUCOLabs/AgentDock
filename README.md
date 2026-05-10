# AgentDock

**AgentDock is a mobile-first command center for AI coding agents.**

It lets developers orchestrate AI coding tasks from a phone or lightweight interface without cloning repositories locally, writing code on a small screen, or keeping a laptop open.

AgentDock connects to GitHub repositories, creates safe working branches, runs AI-assisted development tasks in isolated workspaces, shows diffs and test results, and only commits changes after human approval.

## Vision

AI development is moving from manual coding to agent orchestration.

Developers should be able to:

- Start an AI coding task from anywhere
- Continue an existing agent session
- Review generated changes safely
- Run tests before approving
- Commit to a dedicated branch
- Open a pull request
- Keep full control over what reaches the repository

AgentDock is not a mobile IDE.

It is a control layer for AI coding agents.

## Core Idea

```text
Mobile App / Web Panel
        ↓
AgentDock Backend
        ↓
Isolated Workspace
        ↓
GitHub Repository
        ↓
AI Coding Agent
        ↓
Diff + Tests + Approval
        ↓
Commit + Pull Request
```

## MVP Scope

The first version focuses on a simple and safe workflow:

1. Connect a GitHub repository
2. Create a new agent task
3. Clone the repository into an isolated workspace
4. Create a dedicated working branch
5. Run an AI coding agent with clear instructions
6. Generate a diff
7. Show changed files and test results
8. Ask for human approval
9. Commit and push only after approval
10. Open a pull request

## Safety Principles

AgentDock is designed around safe agentic development:

- Never modify `main` directly
- Always work in a dedicated branch
- Always show the diff before commit
- Never commit without human approval
- Keep logs and task history visible
- Avoid exposing secrets to the agent
- Run tasks in isolated workspaces
- Make every change reviewable

## Planned Features

### MVP

- GitHub repository connection
- Task creation
- Temporary workspace creation
- Branch creation
- Diff generation
- Human approval workflow
- Commit and push
- Pull request creation

### Later

- Mobile app with Expo / React Native
- Web dashboard
- Multiple AI agent providers
- Test runner integration
- Session history
- Notifications
- Team approvals
- Policy rules
- MCP support
- Remote terminal with restricted commands

## Tech Stack

Initial development stack:

- Backend: FastAPI
- Workers: Python subprocess / Docker
- Git Provider: GitHub API
- Database: PostgreSQL or SQLite for MVP
- Queue: Redis / RQ later
- Mobile App: Expo / React Native
- AI Providers: OpenAI first, others later

## Repository Structure

```text
agentdock/
  backend/
    app/
      main.py
      models.py
      github_client.py
      workspace.py
      task_runner.py
      diff_service.py
      agent_runner.py
    requirements.txt

  mobile/
    app/
    package.json

  docker/
    worker.Dockerfile

  README.md
```

## Development Status

AgentDock is currently in early MVP development.

The first goal is to build the backend workflow:

```text
Create task → clone repo → create branch → modify safely → generate diff → approve → commit → push
```

AI integration will be added after the core repository workflow is stable.

## License

TBD
