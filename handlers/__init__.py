"""Сборка всех роутеров."""

from . import (
    accounts,
    admin_claim,
    deploy,
    diff,
    messages,
    models,
    onboarding,
    projects,
    stats,
    system,
    team,
)

ALL_ROUTERS = [
    admin_claim.router,
    accounts.router,
    models.router,
    projects.router,
    system.router,
    onboarding.router,
    stats.router,
    deploy.router,
    diff.router,
    team.router,
    # messages — последним, чтобы команды успели перехватить
    messages.router,
]
