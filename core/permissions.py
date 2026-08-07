from rest_framework import permissions

from .models import User


class IsPlatformAdmin(permissions.BasePermission):
    """Réservé strictement au PLATFORM_ADMIN, y compris en lecture."""
    message = "Réservé à l'administrateur plateforme."

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated
            and request.user.role == User.Role.PLATFORM_ADMIN
        )


class IsStaffSearchRole(permissions.BasePermission):
    """
    Réservé à ceux qui ont un usage légitime de lister/rechercher des comptes
    utilisateurs (par email/rôle) : RH pour ajouter un employé, gérant pour
    ajouter un serveur, ou platform admin. Un EMPLOYEE ou un
    RESTAURANT_SERVER ne doit pas pouvoir lister tous les comptes.
    """
    message = "Vous n'êtes pas autorisé à rechercher des utilisateurs."
    ALLOWED_ROLES = {User.Role.COMPANY_ADMIN, User.Role.RESTAURANT_ADMIN, User.Role.PLATFORM_ADMIN}

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.role in self.ALLOWED_ROLES
        )


class IsPlatformAdminOrReadOnly(permissions.BasePermission):
    """
    Lecture : libre pour tout utilisateur authentifié.
    Écriture (POST/PUT/PATCH/DELETE) : réservée au PLATFORM_ADMIN.
    Utilisé pour Company et Restaurant : seule la plateforme les crée/gère.
    """
    message = "Seul un administrateur plateforme peut effectuer cette action."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.role == User.Role.PLATFORM_ADMIN


class IsRestaurantManagerForObject(permissions.BasePermission):
    """
    Lecture : libre pour tout utilisateur authentifié.
    Écriture (POST/PUT/PATCH/DELETE) : réservée au gérant (manager) du
    restaurant concerné, ou au PLATFORM_ADMIN.

    La vue doit implémenter :
      - get_restaurant_from_request(request) -> Restaurant | None   (utilisé pour POST)
      - get_restaurant_from_object(obj) -> Restaurant | None        (utilisé pour PUT/PATCH/DELETE)
    """
    message = "Seul le gérant du restaurant concerné peut effectuer cette action."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        if request.user.role == User.Role.PLATFORM_ADMIN:
            return True
        if request.user.role != User.Role.RESTAURANT_ADMIN:
            return False
        if request.method == 'POST':
            restaurant = view.get_restaurant_from_request(request)
            return restaurant is not None and request.user in restaurant.managers.all()
        return True  # DELETE/PUT/PATCH : vérification fine dans has_object_permission

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        if request.user.role == User.Role.PLATFORM_ADMIN:
            return True
        if request.user.role != User.Role.RESTAURANT_ADMIN:
            return False
        restaurant = view.get_restaurant_from_object(obj)
        return restaurant is not None and request.user in restaurant.managers.all()


class IsCompanyRHForObject(permissions.BasePermission):
    """
    Lecture : libre pour tout utilisateur authentifié.
    Écriture : réservée au RH (COMPANY_ADMIN) de l'entreprise concernée,
    ou au PLATFORM_ADMIN.

    La vue doit implémenter :
      - get_company_from_request(request) -> Company | None   (utilisé pour POST)
      - get_company_from_object(obj) -> Company | None        (utilisé pour PUT/PATCH/DELETE)
    """
    message = "Seul le RH de l'entreprise concernée peut gérer les employés."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        if request.user.role == User.Role.PLATFORM_ADMIN:
            return True
        if request.user.role != User.Role.COMPANY_ADMIN:
            return False
        if request.method == 'POST':
            company = view.get_company_from_request(request)
            return company is not None and request.user in company.admins.all()
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        if request.user.role == User.Role.PLATFORM_ADMIN:
            return True
        if request.user.role != User.Role.COMPANY_ADMIN:
            return False
        company = view.get_company_from_object(obj)
        return company is not None and request.user in company.admins.all()