import os
import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager


# ==============================================================================
# UTILS & HELPER FUNCTIONS FOR FILE UPLOAD (<uuid>.png)
# ==============================================================================

def upload_uuid_png(instance, filename):
    model_name = instance.__class__.__name__.lower()
    file_uuid = uuid.uuid4()
    return os.path.join(f'uploads/{model_name}/', f'{file_uuid}.png')


# ==============================================================================
# ABSTRACT BASE MODEL WITH UUID PRIMARY KEY
# ==============================================================================

class BaseUUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ==============================================================================
# USER & AUTHENTICATION MODELS
# ==============================================================================

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("L'adresse email est obligatoire")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', User.Role.PLATFORM_ADMIN)
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser, BaseUUIDModel):
    class Role(models.TextChoices):
        PLATFORM_ADMIN = 'PLATFORM_ADMIN', 'Administrateur Plateforme'
        COMPANY_ADMIN = 'COMPANY_ADMIN', 'RH / Admin Entreprise'
        RESTAURANT_ADMIN = 'RESTAURANT_ADMIN', 'Gérant Restaurant'
        RESTAURANT_SERVER = 'RESTAURANT_SERVER', 'Serveur Restaurant'
        EMPLOYEE = 'EMPLOYEE', 'Employé'

    username = None
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    avatar = models.ImageField(upload_to=upload_uuid_png, blank=True, null=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)

    groups = models.ManyToManyField(
        'auth.Group', related_name='custom_user_set', blank=True,
        help_text='The groups this user belongs to.', verbose_name='groups'
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission', related_name='custom_user_permissions_set', blank=True,
        help_text='Specific permissions for this user.', verbose_name='user permissions'
    )

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def __str__(self):
        return f"{self.email} ({self.role})"


# ==============================================================================
# B2B ENTERPRISE & SUBSIDY MODELS
# ==============================================================================

class Company(BaseUUIDModel):
    name = models.CharField(max_length=255)
    tax_id = models.CharField(max_length=50, blank=True, null=True, verbose_name="SIRET/NIF")
    address = models.TextField(blank=True, null=True)
    logo = models.ImageField(upload_to=upload_uuid_png, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    # AJOUT : encours de facturation mensuel (total des subventions dues par l'entreprise)
    total_monthly_bill = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    # AJOUT : RH / administrateurs autorisés à gérer les employés de l'entreprise
    admins = models.ManyToManyField(User, related_name='administered_companies', blank=True)

    def __str__(self):
        return self.name


class SubsidyPolicy(BaseUUIDModel):
    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name='subsidy_policy')
    daily_cap = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Montant de la subvention individuelle par employé et par jour (en FCFA)"
    )
    monthly_allowance_points = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Politique Subvention {self.company.name} ({self.daily_cap} FCFA/jour)"


class EmployeeProfile(BaseUUIDModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='employee_profile')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='employees')
    badge_number = models.CharField(max_length=50, blank=True, null=True)
    department = models.CharField(max_length=100, blank=True, null=True)
    balance_points = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    # AJOUT : mis à True par le RH quand l'employé est en congé -> ne peut plus commander
    on_leave = models.BooleanField(default=False)
    leave_reason = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.email} - {self.company.name}"


# ==============================================================================
# RESTAURANT & MENU MODELS
# ==============================================================================

class Restaurant(BaseUUIDModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    address = models.TextField()
    phone = models.CharField(max_length=20)
    logo = models.ImageField(upload_to=upload_uuid_png, blank=True, null=True)
    cover_image = models.ImageField(upload_to=upload_uuid_png, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    managers = models.ManyToManyField(User, related_name='managed_restaurants', blank=True)
    partner_companies = models.ManyToManyField(Company, related_name='partner_restaurants', blank=True)

    # AJOUT : encours mensuel dû au restaurant par la plateforme (somme des subventions)
    monthly_due = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    def __str__(self):
        return self.name


class RestaurantServer(BaseUUIDModel):
    """
    Serveur enregistré par un restaurant. Permet de tracer/valider quel
    serveur a pris/encaissé une commande (server_id de l'algo).
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='server_profile')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='servers')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.email} - {self.restaurant.name}"


class Category(BaseUUIDModel):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=100)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name_plural = 'Categories'

    def __str__(self):
        return f"{self.restaurant.name} - {self.name}"


class MenuItem(BaseUUIDModel):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.ImageField(upload_to=upload_uuid_png, blank=True, null=True)
    is_available = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.base_price})"


class OptionGroup(BaseUUIDModel):
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='option_groups')
    title = models.CharField(max_length=100)
    is_required = models.BooleanField(default=False)
    max_selection = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.menu_item.name} -> {self.title}"


class OptionExtra(BaseUUIDModel):
    group = models.ForeignKey(OptionGroup, on_delete=models.CASCADE, related_name='extras')
    name = models.CharField(max_length=100)
    additional_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        return f"{self.name} (+{self.additional_price})"


# ==============================================================================
# ORDERS, TICKETS & PAYMENT MODELS
# ==============================================================================

class Order(BaseUUIDModel):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'En attente'
        CONFIRMED = 'CONFIRMED', 'Confirmée'
        PREPARING = 'PREPARING', 'En préparation'
        READY = 'READY', 'Prête'
        COMPLETED = 'COMPLETED', 'Livrée / Récupérée'
        CANCELLED = 'CANCELLED', 'Annulée'

    employee = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT, related_name='orders')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name='orders')

    # AJOUT : serveur ayant traité/validé la commande (server_id de l'algo)
    served_by = models.ForeignKey(
        RestaurantServer, on_delete=models.PROTECT, related_name='orders',
        null=True, blank=True
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    company_subsidy_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    employee_paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    qr_code_token = models.CharField(max_length=255, unique=True, default=uuid.uuid4)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Commande {str(self.id)[:8]} - {self.employee.user.email} ({self.status})"


class OrderItem(BaseUUIDModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    selected_extras = models.ManyToManyField(OptionExtra, blank=True)

    def __str__(self):
        return f"{self.quantity}x {self.menu_item.name} (Commande {str(self.order.id)[:8]})"


class MealTicket(BaseUUIDModel):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='ticket')
    ticket_code = models.CharField(max_length=64, unique=True, default=uuid.uuid4)
    is_redeemed = models.BooleanField(default=False)
    redeemed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Ticket {self.ticket_code} - Redimé: {self.is_redeemed}"


# ==============================================================================
# INVOICING & BILLING MODELS
# ==============================================================================

class Invoice(BaseUUIDModel):
    class Type(models.TextChoices):
        COMPANY_BILL = 'COMPANY_BILL', 'Facture Entreprise (Subventions)'
        RESTAURANT_PAYOUT = 'RESTAURANT_PAYOUT', 'Reversement Restaurant'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Brouillon'
        ISSUED = 'ISSUED', 'Émise'
        PAID = 'PAID', 'Payée'
        OVERDUE = 'OVERDUE', 'En retard'

    invoice_type = models.CharField(max_length=20, choices=Type.choices)
    invoice_number = models.CharField(max_length=100, unique=True)
    company = models.ForeignKey(Company, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices')

    period_start = models.DateField()
    period_end = models.DateField()
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    pdf_file = models.FileField(upload_to=upload_uuid_png, null=True, blank=True)

    def __str__(self):
        return f"Facture {self.invoice_number} ({self.get_invoice_type_display()}) - {self.total_amount}"


# ==============================================================================
# JOURNAL D'AUDIT (Platform Admin)
# ==============================================================================

class AuditLog(BaseUUIDModel):
    """
    Trace des actions sensibles sur la plateforme, consultable uniquement par
    le PLATFORM_ADMIN. Alimenté via la fonction utilitaire core.audit.log_action()
    depuis les points d'entrée critiques (création de compte élevé, ajout
    d'employé, commande, scan, création entreprise/restaurant, subvention...).
    """
    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs'
    )
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=50, blank=True, null=True)
    target_id = models.CharField(max_length=64, blank=True, null=True)
    details = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        who = self.actor.email if self.actor else 'système'
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {who} - {self.action}"