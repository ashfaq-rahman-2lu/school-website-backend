from rest_framework.permissions import BasePermission

from schools.models import SchoolMembership


def membership_for(request):
    if not request.user.is_authenticated or not getattr(request, "tenant", None):
        return None
    return SchoolMembership.objects.filter(user=request.user, school_id=request.tenant.pk, is_active=True).first()


class IsSchoolMember(BasePermission):
    def has_permission(self, request, view):
        return membership_for(request) is not None


class IsSchoolAdmin(IsSchoolMember):
    def has_permission(self, request, view):
        membership = membership_for(request)
        return membership is not None and membership.role == SchoolMembership.Role.ADMIN
