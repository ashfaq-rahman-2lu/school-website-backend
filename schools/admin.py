from django.contrib import admin
from django_tenants.admin import TenantAdminMixin

from .models import Domain, Invitation, LoginIdentity, OTPChallenge, School, SchoolMembership


@admin.register(School)
class SchoolAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("name", "schema_name", "slug", "is_active")
    search_fields = ("name", "slug")


admin.site.register(Domain)
admin.site.register(SchoolMembership)
admin.site.register(Invitation)
admin.site.register(LoginIdentity)
admin.site.register(OTPChallenge)
