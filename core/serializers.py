from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth import authenticate
from rest_framework.exceptions import AuthenticationFailed

from .audit import log_action

from .models import (
    User, Company, SubsidyPolicy, EmployeeProfile, Restaurant, RestaurantServer,
    Category, MenuItem, OptionGroup, OptionExtra, Order, OrderItem,
    MealTicket, Invoice, AuditLog
)


# ==============================================================================
# UTILISATEURS
# ==============================================================================

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'}
    )

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'phone', 'avatar', 'role', 'password']
        extra_kwargs = {
            'password': {'write_only': True}
        }

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        user = User(**validated_data)
        if password is not None:
            user.set_password(password)
        user.save()
        return user


# Représentation légère d'un utilisateur (nom/email/avatar), utilisée en
# lecture seule dans EmployeeProfileSerializer et RestaurantServerSerializer.
# Sans ça, DRF sérialise une relation ForeignKey/OneToOne comme un simple id
# (UUID) — c'est ce qui causait l'affichage de l'id brut au lieu du nom.
class UserMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'avatar', 'role']


# ==============================================================================
# ENTREPRISE & SUBVENTION
# ==============================================================================

class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = [
            'id', 'name', 'tax_id', 'address', 'logo', 'is_active',
            'admins', 'total_monthly_bill', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'total_monthly_bill', 'created_at', 'updated_at']


class SubsidyPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = SubsidyPolicy
        fields = '__all__'


class EmployeeProfileSerializer(serializers.ModelSerializer):
    # 'user' reste un id (écriture, ex: création avec user=<uuid>).
    # 'user_detail' est calculé en lecture seule pour afficher le nom.
    user_detail = UserMiniSerializer(source='user', read_only=True)

    class Meta:
        model = EmployeeProfile
        fields = [
            'id', 'user', 'user_detail', 'company', 'badge_number', 'department',
            'balance_points', 'is_active', 'on_leave', 'leave_reason',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


# ==============================================================================
# RESTAURANT & MENU
# ==============================================================================

class RestaurantServerSerializer(serializers.ModelSerializer):
    user_detail = UserMiniSerializer(source='user', read_only=True)

    class Meta:
        model = RestaurantServer
        fields = ['id', 'user', 'user_detail', 'restaurant', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class OptionExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = OptionExtra
        fields = '__all__'


class OptionGroupSerializer(serializers.ModelSerializer):
    extras = OptionExtraSerializer(many=True, read_only=True)

    class Meta:
        model = OptionGroup
        fields = '__all__'


class MenuItemSerializer(serializers.ModelSerializer):
    option_groups = OptionGroupSerializer(many=True, read_only=True)

    class Meta:
        model = MenuItem
        fields = '__all__'


class CategorySerializer(serializers.ModelSerializer):
    items = MenuItemSerializer(many=True, read_only=True)

    class Meta:
        model = Category
        fields = '__all__'


class RestaurantSerializer(serializers.ModelSerializer):
    # Nested pratique pour l'affichage direct du menu complet si besoin
    # (en plus de l'action dédiée /restaurants/{id}/menu/).
    categories = CategorySerializer(many=True, read_only=True)

    class Meta:
        model = Restaurant
        # fields='__all__' + 'categories' déclaré ci-dessus => inclut aussi
        # automatiquement managers, partner_companies, monthly_due, etc.
        fields = '__all__'
        read_only_fields = ['id', 'monthly_due', 'created_at', 'updated_at']


# ==============================================================================
# COMMANDES & TICKETS
# ==============================================================================

class OrderItemSerializer(serializers.ModelSerializer):
    menu_item_name = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = '__all__'

    def get_menu_item_name(self, obj):
        return obj.menu_item.name


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    # Calculé à partir du MealTicket lié, pour que le frontend puisse
    # réafficher le QR code même après un rafraîchissement de page.
    ticket_code = serializers.SerializerMethodField()
    # Noms lisibles (les champs 'employee'/'restaurant' restent des ids).
    employee_name = serializers.SerializerMethodField()
    restaurant_name = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = '__all__'
        # Protège ces montants : ils ne doivent être calculés que par le
        # workflow place-order / scan-redeem, jamais écrits directement
        # via l'API générique.
        read_only_fields = [
            'id', 'qr_code_token', 'total_amount',
            'company_subsidy_amount', 'employee_paid_amount',
            'created_at', 'updated_at'
        ]

    def get_ticket_code(self, obj):
        ticket = getattr(obj, 'ticket', None)
        return ticket.ticket_code if ticket else None

    def get_employee_name(self, obj):
        return obj.employee.user.get_full_name() or obj.employee.user.email

    def get_restaurant_name(self, obj):
        return obj.restaurant.name


class MealTicketSerializer(serializers.ModelSerializer):
    class Meta:
        model = MealTicket
        fields = '__all__'


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = '__all__'


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ['id', 'actor', 'actor_email', 'action', 'target_type', 'target_id', 'details', 'created_at']

    def get_actor_email(self, obj):
        return obj.actor.email if obj.actor else 'Système'


# ==============================================================================
# AUTHENTIFICATION JWT
# ==============================================================================

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = User.EMAIL_FIELD

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['email'] = user.email
        token['role'] = user.role
        return token

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'] = serializers.EmailField(required=True)
        if 'username' in self.fields:
            del self.fields['username']

    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.get('password')

        if email and password:
            user = authenticate(
                request=self.context.get('request'),
                email=email,
                password=password
            )

            if not user:
                raise AuthenticationFailed(
                    'Aucun compte actif trouvé avec ces identifiants.',
                    code='authorization'
                )

            if not user.is_active:
                raise AuthenticationFailed(
                    'Ce compte est désactivé.',
                    code='authorization'
                )

            refresh = self.get_token(user)

            log_action(user, 'Connexion', target_type='User', target_id=user.id, details=user.email)

            return {
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'email': user.email,
                'role': user.role,
            }
        else:
            raise serializers.ValidationError("L'email et le mot de passe sont requis.")