from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    User, Company, SubsidyPolicy, EmployeeProfile,
    Restaurant, RestaurantServer, Category, MenuItem,
    OptionGroup, OptionExtra, Order, OrderItem,
    MealTicket, Invoice, AuditLog
)


@admin.register(User)
class CustomUserAdmin(BaseUserAdmin):
    list_display = ('email', 'first_name', 'last_name', 'role', 'is_staff', 'is_active')
    list_filter = ('role', 'is_staff', 'is_active')
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Informations personnelles', {'fields': ('first_name', 'last_name', 'phone', 'avatar')}),
        ('Rôles et Permissions', {'fields': ('role', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Dates importantes', {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2', 'first_name', 'last_name', 'role'),
        }),
    )


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'is_active', 'total_monthly_bill', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'tax_id')
    filter_horizontal = ('admins',)
    readonly_fields = ('total_monthly_bill', 'created_at', 'updated_at')


@admin.register(SubsidyPolicy)
class SubsidyPolicyAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'daily_cap', 'monthly_allowance_points')
    list_filter = ('company',)
    search_fields = ('company__name',)


@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'company', 'department', 'is_active', 'on_leave')
    list_filter = ('company', 'is_active', 'on_leave')
    search_fields = ('user__email', 'user__first_name', 'user__last_name', 'badge_number')


@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'phone', 'is_active', 'monthly_due', 'created_at')
    search_fields = ('name', 'phone')
    list_filter = ('is_active',)
    filter_horizontal = ('managers', 'partner_companies')
    readonly_fields = ('monthly_due', 'created_at', 'updated_at')


@admin.register(RestaurantServer)
class RestaurantServerAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'restaurant', 'is_active')
    list_filter = ('restaurant', 'is_active')
    search_fields = ('user__email', 'user__first_name', 'user__last_name')


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'restaurant', 'display_order')
    list_filter = ('restaurant',)


class OptionGroupInline(admin.TabularInline):
    model = OptionGroup
    extra = 0


class OptionExtraInline(admin.TabularInline):
    model = OptionExtra
    extra = 1


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'category', 'base_price', 'is_available')
    list_filter = ('category__restaurant', 'is_available')
    search_fields = ('name',)
    inlines = [OptionGroupInline]


@admin.register(OptionGroup)
class OptionGroupAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'menu_item', 'is_required', 'max_selection')
    list_filter = ('menu_item__category__restaurant', 'is_required')
    inlines = [OptionExtraInline]


@admin.register(OptionExtra)
class OptionExtraAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'group', 'additional_price')
    list_filter = ('group__menu_item__category__restaurant',)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'employee', 'restaurant', 'served_by', 'status',
        'total_amount', 'company_subsidy_amount', 'employee_paid_amount', 'created_at'
    )
    list_filter = ('status', 'restaurant', 'created_at')
    search_fields = ('employee__user__email', 'id')
    inlines = [OrderItemInline]
    readonly_fields = ('qr_code_token', 'created_at', 'updated_at')


@admin.register(MealTicket)
class MealTicketAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'ticket_code', 'is_redeemed', 'redeemed_at')
    list_filter = ('is_redeemed',)
    search_fields = ('ticket_code', 'order__employee__user__email')


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'invoice_number', 'invoice_type', 'company', 'restaurant',
        'period_start', 'period_end', 'total_amount', 'status'
    )
    list_filter = ('invoice_type', 'status', 'company', 'restaurant')
    search_fields = ('invoice_number',)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'action', 'target_type', 'target_id')
    list_filter = ('action', 'target_type')
    search_fields = ('actor__email', 'action', 'details')
    readonly_fields = ('actor', 'action', 'target_type', 'target_id', 'details', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False