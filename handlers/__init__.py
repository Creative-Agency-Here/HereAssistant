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
    remote_control,
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
    remote_control.router,
    # messages — последним, чтобы команды успели перехватить
    messages.router,
]
