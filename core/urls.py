from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from core.serializers import CustomTokenObtainPairSerializer
from .views import (
    UserViewSet, CompanyViewSet, SubsidyPolicyViewSet, EmployeeProfileViewSet,
    RestaurantViewSet, RestaurantServerViewSet, CategoryViewSet, MenuItemViewSet,
    OptionGroupViewSet, OptionExtraViewSet, OrderViewSet,
    MealTicketViewSet, InvoiceViewSet, PlatformStatsViewSet, AuditLogViewSet
)


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


router = DefaultRouter()

router.register(r'users', UserViewSet, basename='user')
router.register(r'companies', CompanyViewSet, basename='company')
router.register(r'subsidy-policies', SubsidyPolicyViewSet, basename='subsidypolicy')
router.register(r'employees', EmployeeProfileViewSet, basename='employeeprofile')
router.register(r'restaurants', RestaurantViewSet, basename='restaurant')
router.register(r'restaurant-servers', RestaurantServerViewSet, basename='restaurantserver')
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'menu-items', MenuItemViewSet, basename='menuitem')
router.register(r'option-groups', OptionGroupViewSet, basename='optiongroup')
router.register(r'option-extras', OptionExtraViewSet, basename='optionextra')
router.register(r'orders', OrderViewSet, basename='order')
router.register(r'meal-tickets', MealTicketViewSet, basename='mealticket')
router.register(r'invoices', InvoiceViewSet, basename='invoice')
router.register(r'platform-stats', PlatformStatsViewSet, basename='platformstats')
router.register(r'audit-logs', AuditLogViewSet, basename='auditlog')

urlpatterns = [
    path('auth/token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('', include(router.urls)),
]