# Task Manager SaaS

A simple project and task management application for developers and small teams.

## Entities

- Project: name (string, required), description (text), status (string), color (string)
- Task: title (string, required), body (text), status (string), priority (string), due_date (datetime), completed (boolean)
- Comment: body (text, required), created_at (datetime)
- Label: name (string, required), color (string, required)

## User Stories

- As a developer, I want to create projects so that I can organise my work.
- As a developer, I want to add tasks to projects so that I can break work into actionable steps.
- As a developer, I want to set task priorities so that I can focus on what matters most.
- As a developer, I want to add comments to tasks so that I can document decisions.
- As a team member, I want to see all my tasks in one place so that I can plan my day.
- As a team member, I want to mark tasks complete so that I can track progress.
- As a manager, I want to see project status so that I can report to stakeholders.

## Integrations

- GitHub (issue sync)
