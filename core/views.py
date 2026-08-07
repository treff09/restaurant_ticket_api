import csv
import io
from datetime import datetime

from django.db import transaction
from django.db.models import Sum, Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image

from .models import (
    User, Company, SubsidyPolicy, EmployeeProfile, Restaurant, RestaurantServer,
    Category, MenuItem, OptionGroup, OptionExtra, Order, OrderItem,
    MealTicket, Invoice, AuditLog
)
from .serializers import (
    UserSerializer, CompanySerializer, SubsidyPolicySerializer, EmployeeProfileSerializer,
    RestaurantSerializer, RestaurantServerSerializer, CategorySerializer, MenuItemSerializer,
    OptionGroupSerializer, OptionExtraSerializer, OrderSerializer,
    OrderItemSerializer, MealTicketSerializer, InvoiceSerializer, AuditLogSerializer
)
from .permissions import (
    IsRestaurantManagerForObject, IsCompanyRHForObject, IsPlatformAdminOrReadOnly,
    IsStaffSearchRole, IsPlatformAdmin
)
from .audit import log_action


def _export_csv(filename, header, rows):
    """Petit utilitaire pour renvoyer un export CSV téléchargeable."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return response


def _export_pdf(filename, entity_name, logo_field, subtitle, summary_rows, table_title, table_header, table_rows):
    """
    Génère un bilan PDF avec logo, réutilisable pour un bilan Company (RH)
    ou Restaurant (gérant). summary_rows et table_rows sont des listes de
    listes (lignes de tableau) déjà formatées en texte.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=30 * mm, bottomMargin=20 * mm,
        leftMargin=20 * mm, rightMargin=20 * mm
    )
    styles = getSampleStyleSheet()
    elements = []

    if logo_field:
        try:
            elements.append(Image(logo_field.path, width=28 * mm, height=28 * mm))
            elements.append(Spacer(1, 10))
        except Exception:
            pass

    elements.append(Paragraph(f"<b>{entity_name}</b>", styles['Title']))
    elements.append(Paragraph(subtitle, styles['Normal']))
    elements.append(Spacer(1, 16))

    summary_table = Table(summary_rows, colWidths=[260, 150])
    summary_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica-Bold'),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 22))

    elements.append(Paragraph(f"<b>{table_title}</b>", styles['Heading3']))
    elements.append(Spacer(1, 8))

    data = [table_header] + table_rows if table_rows else [table_header, ['Aucune donnée sur cette période', '', '']]
    t = Table(data, colWidths=[240, 100, 130])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1B4332')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9.5),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E4DAC0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#FBF7EE')]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(t)

    doc.build(elements)
    buffer.seek(0)
    response = HttpResponse(buffer.read(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


class UserViewSet(viewsets.ModelViewSet):
    """
    L'inscription publique (create, non authentifié) ne peut créer que des
    comptes EMPLOYEE : le rôle envoyé dans la requête est ignoré/forcé.
    Un utilisateur déjà connecté ne peut pas créer un AUTRE compte via cet
    endpoint (ce n'est plus une auto-inscription). Seul un PLATFORM_ADMIN
    garde le droit de créer n'importe quel compte, avec n'importe quel rôle.
    """
    ELEVATED_ROLES = {
        User.Role.COMPANY_ADMIN,
        User.Role.RESTAURANT_ADMIN,
        User.Role.RESTAURANT_SERVER,
        User.Role.PLATFORM_ADMIN,
    }

    queryset = User.objects.all()
    serializer_class = UserSerializer

    def get_permissions(self):
        if self.action == 'create':
            return [permissions.AllowAny()]
        if self.action == 'list':
            return [permissions.IsAuthenticated(), IsStaffSearchRole()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        qs = self.queryset
        email = self.request.query_params.get('email')
        role = self.request.query_params.get('role')
        if email:
            qs = qs.filter(email__icontains=email)
        if role:
            qs = qs.filter(role=role)
        return qs

    def perform_create(self, serializer):
        requested_role = serializer.validated_data.get('role', User.Role.EMPLOYEE)
        caller = self.request.user if self.request.user.is_authenticated else None

        if caller is not None and caller.role != User.Role.PLATFORM_ADMIN:
            raise PermissionDenied(
                "Vous êtes déjà connecté : vous ne pouvez pas créer un autre compte depuis cette session."
            )

        if requested_role in self.ELEVATED_ROLES:
            if not caller or caller.role != User.Role.PLATFORM_ADMIN:
                raise PermissionDenied(
                    "Seul un administrateur plateforme peut créer un compte avec ce rôle."
                )
            user = serializer.save(role=requested_role)
            log_action(
                caller, 'Création compte élevé', target_type='User', target_id=user.id,
                details=f"{user.email} créé avec le rôle {requested_role}"
            )
        else:
            serializer.save(role=User.Role.EMPLOYEE)

    @action(detail=False, methods=['get'])
    def me(self, request):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)


class CompanyViewSet(viewsets.ModelViewSet):
    """
    Lecture libre pour tout utilisateur authentifié. Création/modification/
    suppression réservées au PLATFORM_ADMIN (c'est lui qui onboarde les
    entreprises partenaires).
    """
    queryset = Company.objects.all()
    serializer_class = CompanySerializer
    permission_classes = [permissions.IsAuthenticated, IsPlatformAdminOrReadOnly]

    def perform_create(self, serializer):
        company = serializer.save()
        log_action(self.request.user, 'Création entreprise', target_type='Company', target_id=company.id, details=company.name)

    def perform_update(self, serializer):
        old_active = serializer.instance.is_active
        old_admin_ids = set(serializer.instance.admins.values_list('id', flat=True))

        company = serializer.save()

        if company.is_active != old_active:
            log_action(
                self.request.user,
                'Entreprise activée' if company.is_active else 'Entreprise désactivée',
                target_type='Company', target_id=company.id, details=company.name
            )

        new_admin_ids = set(company.admins.values_list('id', flat=True))
        if new_admin_ids != old_admin_ids:
            log_action(
                self.request.user, 'RH rattaché(s) modifiés', target_type='Company', target_id=company.id,
                details=f"{company.name} : {len(new_admin_ids)} RH rattaché(s)"
            )

    def perform_destroy(self, instance):
        log_action(self.request.user, 'Suppression entreprise', target_type='Company', target_id=instance.id, details=instance.name)
        instance.delete()

    @action(detail=True, methods=['get'], url_path='bilan')
    def bilan(self, request, pk=None):
        """
        Bilan RH : répartition des dépenses / montant dû à chaque restaurant,
        filtrable par année (obligatoire) et mois (optionnel).
        Réservé au RH de l'entreprise ou au PLATFORM_ADMIN.
        ?export=csv pour télécharger le détail par employé en CSV.
        """
        company = self.get_object()
        user = request.user
        is_rh = user.role == User.Role.COMPANY_ADMIN and user in company.admins.all()
        is_platform_admin = user.role == User.Role.PLATFORM_ADMIN
        if not (is_rh or is_platform_admin):
            return Response({'error': "Accès réservé au RH de cette entreprise."}, status=status.HTTP_403_FORBIDDEN)

        year = request.query_params.get('year')
        month = request.query_params.get('month')
        if not year:
            return Response({'error': "Le paramètre 'year' est requis (ex: ?year=2026)."}, status=status.HTTP_400_BAD_REQUEST)

        orders = Order.objects.filter(
            employee__company=company,
            status=Order.Status.COMPLETED,
            ticket__redeemed_at__year=year
        ).select_related('employee__user', 'restaurant')
        if month:
            orders = orders.filter(ticket__redeemed_at__month=month)

        total_subvention = orders.aggregate(total=Sum('company_subsidy_amount'))['total'] or 0

        by_employee = (
            orders.values('employee__id', 'employee__user__email', 'employee__user__first_name', 'employee__user__last_name')
            .annotate(nb_commandes=Count('id'), total_subvention=Sum('company_subsidy_amount'))
            .order_by('-total_subvention')
        )
        by_restaurant = (
            orders.values('restaurant__id', 'restaurant__name')
            .annotate(nb_commandes=Count('id'), total_subvention=Sum('company_subsidy_amount'))
            .order_by('-total_subvention')
        )

        if request.query_params.get('export') == 'csv':
            return _export_csv(
                filename=f"bilan_{company.name}_{year}_{month or 'annuel'}.csv",
                header=['Employé', 'Email', 'Nombre de commandes', 'Total subvention (FCFA)'],
                rows=[[
                    f"{row['employee__user__first_name']} {row['employee__user__last_name']}",
                    row['employee__user__email'], row['nb_commandes'], row['total_subvention']
                ] for row in by_employee]
            )

        if request.query_params.get('export') == 'pdf':
            period_label = f"{month}/{year}" if month else f"Année {year}"
            return _export_pdf(
                filename=f"bilan_{company.name}_{year}_{month or 'annuel'}.pdf",
                entity_name=company.name,
                logo_field=company.logo if company.logo else None,
                subtitle=f"Bilan des subventions repas — {period_label}",
                summary_rows=[
                    ['Subvention totale versée', f"{total_subvention} FCFA"],
                    ['Nombre de commandes servies', str(orders.count())],
                ],
                table_title='Détail par employé',
                table_header=['Employé', 'Commandes', 'Subvention (FCFA)'],
                table_rows=[[
                    f"{row['employee__user__first_name']} {row['employee__user__last_name']}",
                    str(row['nb_commandes']), str(row['total_subvention'])
                ] for row in by_employee]
            )

        return Response({
            'company': company.name,
            'period': {'year': year, 'month': month},
            'total_subvention_versee': total_subvention,
            'nombre_commandes': orders.count(),
            'par_employe': list(by_employee),
            'par_restaurant': list(by_restaurant),
        })


class SubsidyPolicyViewSet(viewsets.ModelViewSet):
    """Seul le RH (COMPANY_ADMIN) de l'entreprise définit/modifie sa politique de subvention."""
    queryset = SubsidyPolicy.objects.all()
    serializer_class = SubsidyPolicySerializer
    permission_classes = [permissions.IsAuthenticated, IsCompanyRHForObject]

    def get_company_from_request(self, request):
        company_id = request.data.get('company')
        return Company.objects.filter(id=company_id).first() if company_id else None

    def get_company_from_object(self, obj):
        return obj.company

    def perform_create(self, serializer):
        policy = serializer.save()
        log_action(
            self.request.user, 'Définition subvention', target_type='SubsidyPolicy', target_id=policy.id,
            details=f"{policy.company.name} : {policy.daily_cap} FCFA/jour"
        )

    def perform_update(self, serializer):
        policy = serializer.save()
        log_action(
            self.request.user, 'Modification subvention', target_type='SubsidyPolicy', target_id=policy.id,
            details=f"{policy.company.name} : {policy.daily_cap} FCFA/jour"
        )


class EmployeeProfileViewSet(viewsets.ModelViewSet):
    """
    Seul le RH (COMPANY_ADMIN) de l'entreprise peut ajouter/modifier/désactiver
    un employé. C'est cette inscription qui donne accès à la subvention.
    """
    queryset = EmployeeProfile.objects.select_related('user', 'company').all()
    serializer_class = EmployeeProfileSerializer
    permission_classes = [permissions.IsAuthenticated, IsCompanyRHForObject]

    def get_queryset(self):
        user = self.request.user
        if user.role == User.Role.COMPANY_ADMIN:
            return self.queryset.filter(company__admins=user)
        if user.role == User.Role.EMPLOYEE and hasattr(user, 'employee_profile'):
            return self.queryset.filter(id=user.employee_profile.id)
        return self.queryset

    def get_company_from_request(self, request):
        company_id = request.data.get('company')
        return Company.objects.filter(id=company_id).first() if company_id else None

    def get_company_from_object(self, obj):
        return obj.company

    def perform_create(self, serializer):
        employee = serializer.save()
        log_action(
            self.request.user, 'Ajout employé', target_type='EmployeeProfile', target_id=employee.id,
            details=f"{employee.user.email} rattaché à {employee.company.name}"
        )

    def perform_update(self, serializer):
        old_active = serializer.instance.is_active
        employee = serializer.save()
        if employee.is_active != old_active:
            log_action(
                self.request.user,
                'Employé activé' if employee.is_active else 'Employé désactivé',
                target_type='EmployeeProfile', target_id=employee.id, details=employee.user.email
            )

    def perform_destroy(self, instance):
        log_action(
            self.request.user, 'Suppression employé', target_type='EmployeeProfile', target_id=instance.id,
            details=instance.user.email
        )
        instance.delete()

    @action(detail=True, methods=['post'], url_path='set-leave')
    def set_leave(self, request, pk=None):
        """Permet au RH de marquer un employé en congé (ou de lever le congé)."""
        employee = self.get_object()
        on_leave = request.data.get('on_leave')
        if on_leave is None:
            return Response({'error': "Le champ 'on_leave' (true/false) est requis."}, status=status.HTTP_400_BAD_REQUEST)

        employee.on_leave = bool(on_leave)
        employee.leave_reason = request.data.get('leave_reason', '') if on_leave else ''
        employee.save(update_fields=['on_leave', 'leave_reason'])

        log_action(
            request.user,
            'Congé activé' if employee.on_leave else 'Congé levé',
            target_type='EmployeeProfile', target_id=employee.id,
            details=employee.leave_reason or ''
        )

        return Response({
            'status': 'SUCCESS',
            'employee_id': employee.id,
            'on_leave': employee.on_leave,
            'leave_reason': employee.leave_reason
        }, status=status.HTTP_200_OK)


class RestaurantViewSet(viewsets.ModelViewSet):
    """
    Lecture libre (tout employé doit pouvoir parcourir les restaurants
    partenaires). Création/modification/suppression réservées au PLATFORM_ADMIN.
    """
    queryset = Restaurant.objects.all()
    serializer_class = RestaurantSerializer
    permission_classes = [permissions.IsAuthenticated, IsPlatformAdminOrReadOnly]

    def perform_create(self, serializer):
        restaurant = serializer.save()
        log_action(self.request.user, 'Création restaurant', target_type='Restaurant', target_id=restaurant.id, details=restaurant.name)

    def perform_update(self, serializer):
        old_active = serializer.instance.is_active
        old_manager_ids = set(serializer.instance.managers.values_list('id', flat=True))

        restaurant = serializer.save()

        if restaurant.is_active != old_active:
            log_action(
                self.request.user,
                'Restaurant activé' if restaurant.is_active else 'Restaurant désactivé',
                target_type='Restaurant', target_id=restaurant.id, details=restaurant.name
            )

        new_manager_ids = set(restaurant.managers.values_list('id', flat=True))
        if new_manager_ids != old_manager_ids:
            log_action(
                self.request.user, 'Gérant(s) rattaché(s) modifiés', target_type='Restaurant', target_id=restaurant.id,
                details=f"{restaurant.name} : {len(new_manager_ids)} gérant(s)"
            )

    def perform_destroy(self, instance):
        log_action(self.request.user, 'Suppression restaurant', target_type='Restaurant', target_id=instance.id, details=instance.name)
        instance.delete()

    @action(detail=True, methods=['get'])
    def menu(self, request, pk=None):
        restaurant = self.get_object()
        categories = Category.objects.filter(restaurant=restaurant).prefetch_related(
            'items__option_groups__extras'
        )
        serializer = CategorySerializer(categories, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='bilan')
    def bilan(self, request, pk=None):
        """
        Bilan Gérant : CA global, montant à recouvrer par entreprise, volume
        de repas, plats les plus vendus. Filtrable par année (obligatoire) et
        mois (optionnel). Réservé au gérant du restaurant ou au PLATFORM_ADMIN.
        ?export=csv pour télécharger le détail par entreprise en CSV.
        """
        restaurant = self.get_object()
        user = request.user
        is_manager = user.role == User.Role.RESTAURANT_ADMIN and user in restaurant.managers.all()
        is_platform_admin = user.role == User.Role.PLATFORM_ADMIN
        if not (is_manager or is_platform_admin):
            return Response({'error': "Accès réservé au gérant de ce restaurant."}, status=status.HTTP_403_FORBIDDEN)

        year = request.query_params.get('year')
        month = request.query_params.get('month')
        if not year:
            return Response({'error': "Le paramètre 'year' est requis (ex: ?year=2026)."}, status=status.HTTP_400_BAD_REQUEST)

        orders = Order.objects.filter(
            restaurant=restaurant,
            status=Order.Status.COMPLETED,
            ticket__redeemed_at__year=year
        ).select_related('employee__company')
        if month:
            orders = orders.filter(ticket__redeemed_at__month=month)

        chiffre_affaires = orders.aggregate(total=Sum('total_amount'))['total'] or 0
        montant_a_recouvrer = orders.aggregate(total=Sum('company_subsidy_amount'))['total'] or 0

        by_company = (
            orders.values('employee__company__id', 'employee__company__name')
            .annotate(nb_repas=Count('id'), montant_du=Sum('company_subsidy_amount'))
            .order_by('-montant_du')
        )
        top_plats = (
            OrderItem.objects.filter(order__in=orders)
            .values('menu_item__name')
            .annotate(quantite_vendue=Sum('quantity'))
            .order_by('-quantite_vendue')[:10]
        )

        if request.query_params.get('export') == 'csv':
            return _export_csv(
                filename=f"bilan_{restaurant.name}_{year}_{month or 'annuel'}.csv",
                header=['Entreprise', 'Nombre de repas', 'Montant dû (FCFA)'],
                rows=[[row['employee__company__name'], row['nb_repas'], row['montant_du']] for row in by_company]
            )

        if request.query_params.get('export') == 'pdf':
            period_label = f"{month}/{year}" if month else f"Année {year}"
            return _export_pdf(
                filename=f"bilan_{restaurant.name}_{year}_{month or 'annuel'}.pdf",
                entity_name=restaurant.name,
                logo_field=restaurant.logo if restaurant.logo else None,
                subtitle=f"Bilan d'activité — {period_label}",
                summary_rows=[
                    ['Chiffre d\'affaires global', f"{chiffre_affaires} FCFA"],
                    ['Montant à recouvrer (subventions)', f"{montant_a_recouvrer} FCFA"],
                    ['Repas servis', str(orders.count())],
                ],
                table_title='Détail par entreprise',
                table_header=['Entreprise', 'Repas', 'Montant dû (FCFA)'],
                table_rows=[[
                    row['employee__company__name'], str(row['nb_repas']), str(row['montant_du'])
                ] for row in by_company]
            )

        return Response({
            'restaurant': restaurant.name,
            'period': {'year': year, 'month': month},
            'chiffre_affaires_global': chiffre_affaires,
            'montant_a_recouvrer_total': montant_a_recouvrer,
            'nombre_repas_servis': orders.count(),
            'par_entreprise': list(by_company),
            'plats_les_plus_vendus': list(top_plats),
        })


class RestaurantServerViewSet(viewsets.ModelViewSet):
    """Seul le gérant (manager) du restaurant peut créer/gérer ses serveurs."""
    queryset = RestaurantServer.objects.select_related('user', 'restaurant').all()
    serializer_class = RestaurantServerSerializer
    permission_classes = [permissions.IsAuthenticated, IsRestaurantManagerForObject]

    def get_queryset(self):
        user = self.request.user
        if user.role == User.Role.RESTAURANT_ADMIN:
            return self.queryset.filter(restaurant__managers=user)
        if user.role == User.Role.RESTAURANT_SERVER:
            return self.queryset.filter(user=user)
        return self.queryset

    @action(detail=False, methods=['get'], url_path='me')
    def me(self, request):
        """Le serveur connecté récupère son propre profil (id, restaurant...)."""
        if not hasattr(request.user, 'server_profile'):
            return Response({'error': "Vous n'avez pas de profil serveur."}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(request.user.server_profile)
        return Response(serializer.data)

    def get_restaurant_from_request(self, request):
        restaurant_id = request.data.get('restaurant')
        return Restaurant.objects.filter(id=restaurant_id).first() if restaurant_id else None

    def perform_update(self, serializer):
        old_active = serializer.instance.is_active
        server = serializer.save()
        if server.is_active != old_active:
            log_action(
                self.request.user,
                'Serveur activé' if server.is_active else 'Serveur désactivé',
                target_type='RestaurantServer', target_id=server.id, details=server.user.email
            )

    def perform_destroy(self, instance):
        log_action(
            self.request.user, 'Suppression serveur', target_type='RestaurantServer', target_id=instance.id,
            details=instance.user.email
        )
        instance.delete()

    def get_restaurant_from_object(self, obj):
        return obj.restaurant

    @action(
        detail=False, methods=['post'], url_path='create-with-account',
        permission_classes=[permissions.IsAuthenticated]
    )
    def create_with_account(self, request):
        """
        Le gérant crée en un seul appel le compte de connexion ET le profil
        serveur, tous deux rattachés à son restaurant.
        ENTRÉE : restaurant_id, email, password, first_name, last_name
        """
        user = request.user
        if user.role not in (User.Role.RESTAURANT_ADMIN, User.Role.PLATFORM_ADMIN):
            return Response({'error': "Seul un gérant peut créer un compte serveur."}, status=status.HTTP_403_FORBIDDEN)

        restaurant_id = request.data.get('restaurant_id')
        email = request.data.get('email')
        password = request.data.get('password')
        first_name = request.data.get('first_name', '')
        last_name = request.data.get('last_name', '')

        if not all([restaurant_id, email, password]):
            return Response(
                {'error': "restaurant_id, email et password sont requis."},
                status=status.HTTP_400_BAD_REQUEST
            )

        restaurant = get_object_or_404(Restaurant, id=restaurant_id)

        if user.role == User.Role.RESTAURANT_ADMIN and user not in restaurant.managers.all():
            return Response({'error': "Vous ne gérez pas ce restaurant."}, status=status.HTTP_403_FORBIDDEN)

        if User.objects.filter(email=email).exists():
            return Response({'error': "Un compte existe déjà avec cet email."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                new_user = User.objects.create_user(
                    email=email,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    role=User.Role.RESTAURANT_SERVER
                )
                server = RestaurantServer.objects.create(user=new_user, restaurant=restaurant)

            log_action(
                request.user, 'Création compte serveur', target_type='RestaurantServer', target_id=server.id,
                details=f"{new_user.email} pour {restaurant.name}"
            )
        except Exception as e:
            return Response(
                {'error': f"Erreur lors de la création du compte : {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({
            'status': 'SUCCESS',
            'server_id': server.id,
            'user_id': new_user.id,
            'email': new_user.email
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='bilan-jour')
    def bilan_jour(self, request, pk=None):
        """
        Suivi personnel du serveur : commandes qu'il a scannées/servies
        aujourd'hui (ou à une date donnée via ?date=YYYY-MM-DD).
        Accessible au serveur lui-même, au gérant de son restaurant, ou au PLATFORM_ADMIN.
        """
        server = self.get_object()
        user = request.user
        is_self = user.id == server.user_id
        is_manager = user.role == User.Role.RESTAURANT_ADMIN and user in server.restaurant.managers.all()
        is_platform_admin = user.role == User.Role.PLATFORM_ADMIN
        if not (is_self or is_manager or is_platform_admin):
            return Response({'error': "Accès non autorisé."}, status=status.HTTP_403_FORBIDDEN)

        date_str = request.query_params.get('date')
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                return Response({'error': "Format de date invalide, attendu YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            target_date = timezone.now().date()

        orders = Order.objects.filter(
            served_by=server, ticket__redeemed_at__date=target_date
        ).select_related('employee__user', 'ticket')

        commandes = [{
            'order_id': o.id,
            'ticket_code': o.ticket.ticket_code,
            'employee': o.employee.user.get_full_name() or o.employee.user.email,
            'total_amount': o.total_amount,
            'amount_collected_cash': o.employee_paid_amount,
            'redeemed_at': o.ticket.redeemed_at,
        } for o in orders]

        return Response({
            'date': str(target_date),
            'server': server.user.get_full_name() or server.user.email,
            'restaurant': server.restaurant.name,
            'total_commandes_servies': orders.count(),
            'total_encaisse_especes': sum((o.employee_paid_amount for o in orders), start=0),
            'commandes': commandes,
        })


class CategoryViewSet(viewsets.ModelViewSet):
    """Seul le gérant du restaurant peut créer/modifier/supprimer ses catégories."""
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated, IsRestaurantManagerForObject]

    def get_restaurant_from_request(self, request):
        restaurant_id = request.data.get('restaurant')
        return Restaurant.objects.filter(id=restaurant_id).first() if restaurant_id else None

    def get_restaurant_from_object(self, obj):
        return obj.restaurant

    def perform_create(self, serializer):
        category = serializer.save()
        log_action(
            self.request.user, 'Création catégorie menu', target_type='Category', target_id=category.id,
            details=f"{category.name} ({category.restaurant.name})"
        )

    def perform_destroy(self, instance):
        log_action(
            self.request.user, 'Suppression catégorie menu', target_type='Category', target_id=instance.id,
            details=f"{instance.name} ({instance.restaurant.name})"
        )
        instance.delete()


class MenuItemViewSet(viewsets.ModelViewSet):
    """Seul le gérant du restaurant propriétaire de la catégorie peut ajouter/modifier/supprimer un plat."""
    queryset = MenuItem.objects.select_related('category__restaurant').all()
    serializer_class = MenuItemSerializer
    permission_classes = [permissions.IsAuthenticated, IsRestaurantManagerForObject]

    def get_restaurant_from_request(self, request):
        category = Category.objects.filter(id=request.data.get('category')).select_related('restaurant').first()
        return category.restaurant if category else None

    def get_restaurant_from_object(self, obj):
        return obj.category.restaurant

    def perform_create(self, serializer):
        item = serializer.save()
        log_action(
            self.request.user, 'Création plat', target_type='MenuItem', target_id=item.id,
            details=f"{item.name} ({item.category.restaurant.name})"
        )

    def perform_update(self, serializer):
        old_available = serializer.instance.is_available
        item = serializer.save()
        if item.is_available != old_available:
            log_action(
                self.request.user,
                'Plat activé' if item.is_available else 'Plat désactivé',
                target_type='MenuItem', target_id=item.id, details=item.name
            )

    def perform_destroy(self, instance):
        log_action(
            self.request.user, 'Suppression plat', target_type='MenuItem', target_id=instance.id,
            details=instance.name
        )
        instance.delete()


class OptionGroupViewSet(viewsets.ModelViewSet):
    """Seul le gérant du restaurant propriétaire du plat peut gérer ses groupes d'options."""
    queryset = OptionGroup.objects.select_related('menu_item__category__restaurant').all()
    serializer_class = OptionGroupSerializer
    permission_classes = [permissions.IsAuthenticated, IsRestaurantManagerForObject]

    def get_restaurant_from_request(self, request):
        item = MenuItem.objects.filter(id=request.data.get('menu_item')).select_related('category__restaurant').first()
        return item.category.restaurant if item else None

    def get_restaurant_from_object(self, obj):
        return obj.menu_item.category.restaurant

    def perform_create(self, serializer):
        group = serializer.save()
        log_action(
            self.request.user, "Création groupe d'options", target_type='OptionGroup', target_id=group.id,
            details=f"{group.title} ({group.menu_item.name})"
        )

    def perform_destroy(self, instance):
        log_action(
            self.request.user, "Suppression groupe d'options", target_type='OptionGroup', target_id=instance.id,
            details=f"{instance.title} ({instance.menu_item.name})"
        )
        instance.delete()


class OptionExtraViewSet(viewsets.ModelViewSet):
    """Seul le gérant du restaurant propriétaire du groupe peut gérer ses suppléments."""
    queryset = OptionExtra.objects.select_related('group__menu_item__category__restaurant').all()
    serializer_class = OptionExtraSerializer
    permission_classes = [permissions.IsAuthenticated, IsRestaurantManagerForObject]

    def get_restaurant_from_request(self, request):
        group = OptionGroup.objects.filter(id=request.data.get('group')).select_related(
            'menu_item__category__restaurant'
        ).first()
        return group.menu_item.category.restaurant if group else None

    def get_restaurant_from_object(self, obj):
        return obj.group.menu_item.category.restaurant

    def perform_create(self, serializer):
        extra = serializer.save()
        log_action(
            self.request.user, 'Ajout supplément', target_type='OptionExtra', target_id=extra.id,
            details=f"{extra.name} (+{extra.additional_price} FCFA) dans {extra.group.title}"
        )

    def perform_destroy(self, instance):
        log_action(
            self.request.user, 'Suppression supplément', target_type='OptionExtra', target_id=instance.id,
            details=instance.name
        )
        instance.delete()


class MealTicketViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MealTicket.objects.all()
    serializer_class = MealTicketSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='lookup')
    def lookup(self, request):
        """
        Étape 1 du scan : le serveur consulte le détail de la commande
        AVANT de la valider (rien n'est modifié ici, lecture seule).
        Permet d'afficher le plat, l'accompagnement, les suppléments et le
        montant à encaisser avant de confirmer.
        """
        user = request.user
        if user.role != User.Role.RESTAURANT_SERVER or not hasattr(user, 'server_profile'):
            return Response({'error': "Seul un serveur peut consulter un ticket."}, status=status.HTTP_403_FORBIDDEN)

        server = user.server_profile
        ticket_code = request.data.get('ticket_code')
        if not ticket_code:
            return Response({'error': 'Le code du ticket est requis.'}, status=status.HTTP_400_BAD_REQUEST)

        ticket = get_object_or_404(MealTicket, ticket_code=ticket_code)

        if ticket.order.restaurant_id != server.restaurant_id:
            return Response({'error': "Ce ticket ne correspond pas à votre restaurant."}, status=status.HTTP_400_BAD_REQUEST)

        order = ticket.order
        items = order.items.select_related('menu_item').prefetch_related('selected_extras__group')

        def group_extras(item):
            """Regroupe les suppléments sélectionnés par groupe d'options
            (ex: 'Accompagnement' obligatoire vs 'Suppléments' optionnel),
            pour que le serveur voie clairement quoi servir avec le plat."""
            groups = {}
            for extra in item.selected_extras.all():
                key = extra.group_id
                if key not in groups:
                    groups[key] = {
                        'group_title': extra.group.title,
                        'is_required': extra.group.is_required,
                        'choices': []
                    }
                groups[key]['choices'].append(extra.name)
            return list(groups.values())

        return Response({
            'ticket_code': ticket.ticket_code,
            'order_id': order.id,
            'is_redeemed': ticket.is_redeemed,
            'status': order.status,
            'employee': order.employee.user.get_full_name() or order.employee.user.email,
            'items': [{
                'dish': item.menu_item.name,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'extras_groups': group_extras(item),
            } for item in items],
            'total_order': order.total_amount,
            'company_subvention': order.company_subsidy_amount,
            'amount_to_collect_cash': order.employee_paid_amount,
        })

    @action(detail=False, methods=['post'], url_path='scan-redeem')
    def scan_redeem(self, request):
        """
        Étape 2 du workflow : le SERVEUR scanne le QR code présenté par
        l'employé. Marque le ticket utilisé + la commande "Servie", enregistre
        quel serveur a servi (served_by), et incrémente les encours
        restaurant/entreprise (c'est à CE moment, pas à la commande, que la
        subvention devient effectivement due).
        """
        user = request.user
        if user.role != User.Role.RESTAURANT_SERVER or not hasattr(user, 'server_profile'):
            return Response({'error': "Seul un serveur peut valider un ticket."}, status=status.HTTP_403_FORBIDDEN)

        server = user.server_profile
        if not server.is_active:
            return Response({'error': "Votre compte serveur est inactif."}, status=status.HTTP_403_FORBIDDEN)

        ticket_code = request.data.get('ticket_code')
        if not ticket_code:
            return Response({'error': 'Le code du ticket est requis.'}, status=status.HTTP_400_BAD_REQUEST)

        ticket = get_object_or_404(MealTicket, ticket_code=ticket_code)

        if ticket.order.restaurant_id != server.restaurant_id:
            return Response({'error': "Ce ticket ne correspond pas à votre restaurant."}, status=status.HTTP_400_BAD_REQUEST)

        if ticket.is_redeemed:
            return Response({'error': 'Ce ticket a déjà été utilisé.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                ticket.is_redeemed = True
                ticket.redeemed_at = timezone.now()
                ticket.save(update_fields=['is_redeemed', 'redeemed_at'])

                order = ticket.order
                order.status = Order.Status.COMPLETED
                order.served_by = server
                order.save(update_fields=['status', 'served_by'])

                company = Company.objects.select_for_update().get(id=order.employee.company_id)
                company.total_monthly_bill += order.company_subsidy_amount
                company.save(update_fields=['total_monthly_bill'])

                restaurant = Restaurant.objects.select_for_update().get(id=order.restaurant_id)
                restaurant.monthly_due += order.company_subsidy_amount
                restaurant.save(update_fields=['monthly_due'])

            log_action(
                request.user, 'Ticket validé (servie)', target_type='Order', target_id=order.id,
                details=f"{ticket.ticket_code} - {order.employee.user.email}"
            )

        except Exception as e:
            return Response(
                {'error': f"Erreur lors de la validation du ticket : {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({
            'status': 'SUCCESS',
            'message': 'Ticket validé, commande marquée Servie',
            'ticket_code': ticket.ticket_code,
            'order_id': order.id,
            'employee': order.employee.user.get_full_name() or order.employee.user.email,
            'total_order': order.total_amount,
            'amount_to_collect_cash': order.employee_paid_amount,
            'display': f"ENCAISSER : {order.employee_paid_amount} FCFA"
        }, status=status.HTTP_200_OK)


class InvoiceViewSet(viewsets.ModelViewSet):
    queryset = Invoice.objects.all()
    serializer_class = InvoiceSerializer
    permission_classes = [permissions.IsAuthenticated]


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.prefetch_related('items').all()
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = self.queryset
        if user.role == User.Role.EMPLOYEE and hasattr(user, 'employee_profile'):
            return qs.filter(employee=user.employee_profile)
        if user.role == User.Role.RESTAURANT_ADMIN:
            return qs.filter(restaurant__managers=user)
        if user.role == User.Role.RESTAURANT_SERVER and hasattr(user, 'server_profile'):
            return qs.filter(restaurant=user.server_profile.restaurant)
        if user.role == User.Role.COMPANY_ADMIN:
            return qs.filter(employee__company__admins=user)
        return qs

    @action(detail=False, methods=['post'], url_path='place-order')
    def place_order(self, request):
        """
        Étape 1 du workflow : l'EMPLOYÉ connecté commande pour lui-même.
        ENTRÉE : restaurant_id, item_id, array_supplements[],
                 current_date (optionnel, YYYY-MM-DD).
        Aucun server_id ici : le serveur n'intervient qu'au scan (étape 2).
        Crée la commande en PENDING + le ticket QR non consommé. Les
        montants (subvention / reste à payer) sont calculés et stockés
        immédiatement, mais les encours restaurant/entreprise ne sont
        incrémentés qu'au moment du scan (scan-redeem), pas ici.
        """
        user = request.user
        if user.role != User.Role.EMPLOYEE or not hasattr(user, 'employee_profile'):
            return Response({'error': "Seul un employé peut passer commande."}, status=status.HTTP_403_FORBIDDEN)

        employee = user.employee_profile
        restaurant_id = request.data.get('restaurant_id')
        item_id = request.data.get('item_id')
        array_supplements = request.data.get('array_supplements', [])

        current_date_str = request.data.get('current_date')
        if current_date_str:
            try:
                current_date = datetime.strptime(current_date_str, '%Y-%m-%d').date()
            except ValueError:
                return Response(
                    {'error': "Format de current_date invalide, attendu YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            current_date = timezone.now().date()

        if not all([restaurant_id, item_id]):
            return Response(
                {'error': "restaurant_id et item_id sont requis."},
                status=status.HTTP_400_BAD_REQUEST
            )

        restaurant = get_object_or_404(Restaurant, id=restaurant_id)
        menu_item = get_object_or_404(MenuItem, id=item_id, category__restaurant=restaurant)

        # -------------------------------------------------------------
        # 1. VÉRIFIER la validité de l'employé et du ticket journalier
        # -------------------------------------------------------------
        if not employee.is_active:
            return Response({'error': "Votre profil employé est inactif."}, status=status.HTTP_400_BAD_REQUEST)

        if employee.on_leave:
            return Response(
                {'error': "Vous êtes marqué en congé par le RH, commande impossible."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Non-cumulabilité : un seul ticket par jour (utilisé ou non) -> si
        # l'employé n'a pas commandé aujourd'hui, ce jour est simplement
        # perdu, jamais reporté au lendemain.
        already_ordered_today = Order.objects.filter(
            employee=employee, created_at__date=current_date
        ).exclude(status=Order.Status.CANCELLED).exists()

        if already_ordered_today:
            return Response(
                {'error': "Vous avez déjà commandé aujourd'hui. Le ticket n'est pas cumulable d'un jour à l'autre."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # -------------------------------------------------------------
        # 2. CALCULER le montant total de la commande
        # -------------------------------------------------------------
        base_price = menu_item.base_price

        extras = OptionExtra.objects.filter(id__in=array_supplements)
        supplements_price = sum((extra.additional_price for extra in extras), start=0)

        total_order = base_price + supplements_price

        # -------------------------------------------------------------
        # 3. CALCULER la ventilation du paiement (Subvention)
        # -------------------------------------------------------------
        subsidy_policy = getattr(employee.company, 'subsidy_policy', None)
        daily_quota = subsidy_policy.daily_cap if subsidy_policy else 0

        company_subvention = min(total_order, daily_quota)
        amount_to_collect_cash = max(0, total_order - company_subvention)

        # -------------------------------------------------------------
        # 4. CRÉER la commande + le ticket QR (transaction atomique)
        # -------------------------------------------------------------
        try:
            with transaction.atomic():
                order = Order.objects.create(
                    employee=employee,
                    restaurant=restaurant,
                    total_amount=total_order,
                    company_subsidy_amount=company_subvention,
                    employee_paid_amount=amount_to_collect_cash,
                    status=Order.Status.PENDING
                )

                order_item = OrderItem.objects.create(
                    order=order,
                    menu_item=menu_item,
                    quantity=1,
                    unit_price=base_price
                )
                if array_supplements:
                    order_item.selected_extras.set(extras)

                ticket = MealTicket.objects.create(
                    order=order,
                    ticket_code=f"TICK-{order.id}-{timezone.now().strftime('%Y%m%d%H%M%S')}",
                    is_redeemed=False
                )

            log_action(
                request.user, 'Commande passée', target_type='Order', target_id=order.id,
                details=f"{menu_item.name} chez {restaurant.name}, {total_order} FCFA"
            )

        except Exception as e:
            return Response(
                {'error': f"Erreur lors de la création de la commande : {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # -------------------------------------------------------------
        # 5. RETOURNER À L'EMPLOYÉ (ticket QR à présenter au restaurant)
        # -------------------------------------------------------------
        return Response({
            'status': 'SUCCESS',
            'message': 'Commande créée avec succès. Présentez ce ticket au restaurant.',
            'order_id': order.id,
            'ticket_code': ticket.ticket_code,
            'total_order': total_order,
            'company_subvention': company_subvention,
            'amount_to_collect_cash': amount_to_collect_cash,
            'display': f"À PAYER SUR PLACE : {amount_to_collect_cash} FCFA"
        }, status=status.HTTP_201_CREATED)


class PlatformStatsViewSet(viewsets.ViewSet):
    """Vue d'ensemble globale de la plateforme, réservée au PLATFORM_ADMIN."""
    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]

    def list(self, request):
        today = timezone.now().date()

        orders_today = Order.objects.filter(created_at__date=today)
        orders_completed_today = orders_today.filter(status=Order.Status.COMPLETED)

        return Response({
            'companies_count': Company.objects.count(),
            'companies_active_count': Company.objects.filter(is_active=True).count(),
            'restaurants_count': Restaurant.objects.count(),
            'restaurants_active_count': Restaurant.objects.filter(is_active=True).count(),
            'employees_count': EmployeeProfile.objects.count(),
            'servers_count': RestaurantServer.objects.count(),
            'orders_today_count': orders_today.count(),
            'orders_completed_today_count': orders_completed_today.count(),
            'revenue_today': orders_completed_today.aggregate(total=Sum('total_amount'))['total'] or 0,
            'subsidy_today': orders_completed_today.aggregate(total=Sum('company_subsidy_amount'))['total'] or 0,
            'total_platform_bill': Company.objects.aggregate(total=Sum('total_monthly_bill'))['total'] or 0,
        })


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Journal d'audit, lecture seule, réservé au PLATFORM_ADMIN."""
    queryset = AuditLog.objects.select_related('actor').all()
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]

    def get_queryset(self):
        qs = self.queryset
        action = self.request.query_params.get('action')
        target_type = self.request.query_params.get('target_type')
        if action:
            qs = qs.filter(action__icontains=action)
        if target_type:
            qs = qs.filter(target_type=target_type)
        return qs[:500]