from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context

from schools.models import Domain, School


class Command(BaseCommand):
    help = "Create the public tenant and local domain used for platform administration."

    def handle(self, *args, **options):
        with schema_context("public"):
            platform, _ = School.objects.get_or_create(
                schema_name="public",
                defaults={"name": "Schoolwise Platform", "slug": "platform", "is_active": True},
            )
            Domain.objects.get_or_create(domain="localhost", defaults={"tenant": platform, "is_primary": True})
            self.stdout.write(self.style.SUCCESS("Public tenant ready at http://localhost:8000/admin/"))
