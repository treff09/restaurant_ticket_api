"""
Petit utilitaire pour journaliser les actions sensibles de la plateforme.
Ne lève jamais d'exception : un souci de log ne doit jamais faire planter
la requête métier qui l'a déclenché.
"""

from .models import AuditLog


def log_action(actor, action, target_type=None, target_id=None, details=None):
    try:
        AuditLog.objects.create(
            actor=actor if (actor and getattr(actor, 'is_authenticated', False)) else None,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id else None,
            details=details or ''
        )
    except Exception:
        pass