from django.contrib.auth import get_user_model
from django_tenants.models import DomainMixin, TenantMixin
from django.db import models


class School(TenantMixin):
    name = models.CharField(max_length=180)
    slug = models.SlugField(max_length=80, unique=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    auto_create_schema = True

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    pass


class SchoolMembership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        TEACHER = "teacher", "Teacher"

    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name="school_memberships")
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.TEACHER)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "school"], name="unique_user_school_membership"),
            models.UniqueConstraint(fields=["user"], condition=models.Q(is_active=True), name="one_active_school_per_staff_user"),
        ]


class Invitation(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=SchoolMembership.Role.choices, default=SchoolMembership.Role.TEACHER)
    token_hash = models.CharField(max_length=128, unique=True)
    invited_by = models.ForeignKey(get_user_model(), on_delete=models.PROTECT, related_name="sent_school_invitations")
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class LoginIdentity(models.Model):
    class Kind(models.TextChoices):
        EMAIL = "email", "Email"
        PHONE = "phone", "Phone"

    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name="login_identities")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    value = models.CharField(max_length=254)
    is_verified = models.BooleanField(default=False)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "value"], name="unique_login_identity")]


class OTPChallenge(models.Model):
    reference = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name="otp_challenges")
    identity = models.ForeignKey(LoginIdentity, on_delete=models.CASCADE, related_name="challenges")
    code_digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
