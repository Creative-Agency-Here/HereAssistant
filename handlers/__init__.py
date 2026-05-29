"""Сборка всех роутеров."""

from . import admin_claim, accounts, models, projects, system, stats, deploy, diff, messages

ALL_ROUTERS = [
    admin_claim.router,
    accounts.router,
    models.router,
    projects.router,
    system.router,
    stats.router,
    deploy.router,
    diff.router,
    # messages — последним, чтобы команды успели перехватить
    messages.router,
]
