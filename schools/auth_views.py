import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.views.decorators.csrf import csrf_protect
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import LoginIdentity, OTPChallenge, SchoolMembership
from .utils import frontend_base_url


def _tenant_access(user, request):
    tenant = getattr(request, "tenant", None)
    if tenant is None or getattr(tenant, "schema_name", "public") == "public":
        return False
    staff = SchoolMembership.objects.filter(user=user, school_id=tenant.pk, is_active=True).exists()
    if staff:
        return True
    # The tenant schema's StudentPortalAccess table maps guardian/student accounts to permitted students.
    from academics.models import StudentPortalAccess
    return StudentPortalAccess.objects.filter(user=user, student__is_active=True).exists()


def _digest(code):
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def _deliver(identity, code):
    if identity.kind == LoginIdentity.Kind.EMAIL:
        send_mail("Your Schoolwise sign-in code", f"Your verification code is {code}. It expires in five minutes.", settings.DEFAULT_FROM_EMAIL, [identity.value], fail_silently=False)
        return
    backend_path = getattr(settings, "SMS_BACKEND", "")
    if not backend_path:
        raise ValueError("Phone OTP delivery is not configured. Configure an SMS_BACKEND before enabling phone login.")
    module_name, class_name = backend_path.rsplit(".", 1)
    backend = getattr(__import__(module_name, fromlist=[class_name]), class_name)()
    backend.send(identity.value, f"Your Schoolwise sign-in code is {code}.")


@api_view(["GET"])
@permission_classes([AllowAny])
def csrf(request):
    return Response({"csrfToken": get_token(request)})


@csrf_protect
@api_view(["POST"])
@permission_classes([AllowAny])
def password_login(request):
    identifier = str(request.data.get("identifier", "")).strip()
    password = str(request.data.get("password", ""))
    if not identifier or not password:
        return Response({"detail": "Enter your email or username and password."}, status=status.HTTP_400_BAD_REQUEST)
    User = get_user_model()
    user = User.objects.filter(email__iexact=identifier).first() or User.objects.filter(username__iexact=identifier).first()
    authenticated = authenticate(request, username=user.get_username(), password=password) if user else None
    if not authenticated or not authenticated.is_active or not _tenant_access(authenticated, request):
        return Response({"detail": "Email, username, or password is incorrect, or this account cannot access this school."}, status=status.HTTP_400_BAD_REQUEST)
    login(request, authenticated)
    return Response({"user": _profile(authenticated, request)})


@csrf_protect
@api_view(["POST"])
@permission_classes([AllowAny])
def otp_start(request):
    identifier = str(request.data.get("identifier", "")).strip()
    channel = request.data.get("channel", "email")
    kind = LoginIdentity.Kind.EMAIL if channel == "email" else LoginIdentity.Kind.PHONE if channel == "phone" else None
    if not identifier or kind is None:
        return Response({"detail": "Provide an email or phone number."}, status=status.HTTP_400_BAD_REQUEST)
    if kind == LoginIdentity.Kind.PHONE and not getattr(settings, "SMS_BACKEND", ""):
        return Response({"detail": "Phone sign-in is not configured for this school yet."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    identity = LoginIdentity.objects.select_related("user").filter(kind=kind, value__iexact=identifier, user__is_active=True).first()
    reference = secrets.token_urlsafe(24)
    if identity and _tenant_access(identity.user, request):
        recent = OTPChallenge.objects.filter(identity=identity, created_at__gte=timezone.now() - timedelta(minutes=15)).count()
        if recent < 5:
            code = f"{secrets.randbelow(1_000_000):06d}"
            challenge = OTPChallenge.objects.create(reference=reference, user=identity.user, identity=identity, code_digest=_digest(code), expires_at=timezone.now() + timedelta(seconds=settings.OTP_TTL_SECONDS))
            try:
                _deliver(identity, code)
            except Exception:
                challenge.delete()
                return Response({"detail": "This sign-in method is temporarily unavailable."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    # Do not reveal whether a school account or contact exists.
    return Response({"detail": "If this contact is registered for this school, a sign-in code has been sent.", "challenge_id": reference})


@csrf_protect
@api_view(["POST"])
@permission_classes([AllowAny])
def otp_verify(request):
    challenge_id = request.data.get("challenge_id")
    code = str(request.data.get("code", ""))
    challenge = OTPChallenge.objects.select_related("user").filter(reference=challenge_id, consumed_at__isnull=True, expires_at__gt=timezone.now()).first()
    if not challenge or challenge.attempts >= 5 or not hmac.compare_digest(challenge.code_digest, _digest(code)):
        if challenge:
            OTPChallenge.objects.filter(pk=challenge.pk).update(attempts=challenge.attempts + 1)
        return Response({"detail": "The code is invalid or expired."}, status=status.HTTP_400_BAD_REQUEST)
    if not _tenant_access(challenge.user, request):
        return Response({"detail": "This account cannot access this school."}, status=status.HTTP_403_FORBIDDEN)
    OTPChallenge.objects.filter(pk=challenge.pk).update(consumed_at=timezone.now())
    if not challenge.identity.is_verified:
        LoginIdentity.objects.filter(pk=challenge.identity_id).update(is_verified=True)
    login(request, challenge.user)
    return Response({"user": _profile(challenge.user, request)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_user(request):
    logout(request)
    return Response({"detail": "Signed out."})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def current_user(request):
    if not _tenant_access(request.user, request):
        return Response({"detail": "This account cannot access this school."}, status=status.HTTP_403_FORBIDDEN)
    return Response(_profile(request.user, request))


def _profile(user, request):
    membership = SchoolMembership.objects.filter(user=user, school=request.tenant, is_active=True).first()
    from academics.models import StudentPortalAccess
    portal_access = StudentPortalAccess.objects.filter(user=user, student__is_active=True).exists()
    return {"id": user.pk, "name": user.get_full_name() or user.get_username(), "email": user.email, "role": membership.role if membership else "student_guardian", "portal_access": portal_access}


@csrf_protect
@api_view(["POST"])
@permission_classes([AllowAny])
def password_reset_request(request):
    contact = str(request.data.get("email", "")).strip()
    User = get_user_model()
    user = User.objects.filter(email__iexact=contact, is_active=True).first() or User.objects.filter(username__iexact=contact, is_active=True).first()
    identity = LoginIdentity.objects.filter(user=user, kind=LoginIdentity.Kind.EMAIL, value__iexact=contact, is_verified=True).exists() if user else False
    if user and identity:
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        link = f"{frontend_base_url(request)}/reset-password?uid={uid}&token={token}"
        send_mail("Reset your Schoolwise password", f"Use this link to set a new password: {link}\n\nIf you did not request this, ignore this email.", settings.DEFAULT_FROM_EMAIL, [contact], fail_silently=False)
    return Response({"detail": "If an account exists for that email, password reset instructions have been sent."})


@csrf_protect
@api_view(["POST"])
@permission_classes([AllowAny])
def password_reset_confirm(request):
    uid, token, password = request.data.get("uid"), request.data.get("token"), request.data.get("password", "")
    try:
        user_id = force_str(urlsafe_base64_decode(uid))
        user = get_user_model().objects.get(pk=user_id, is_active=True)
    except Exception:
        return Response({"detail": "This reset link is invalid or expired."}, status=status.HTTP_400_BAD_REQUEST)
    if not default_token_generator.check_token(user, token):
        return Response({"detail": "This reset link is invalid or expired."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        validate_password(password, user)
    except Exception as exc:
        return Response({"detail": list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
    user.set_password(password)
    user.save(update_fields=["password"])
    LoginIdentity.objects.filter(user=user, kind=LoginIdentity.Kind.EMAIL, is_primary=True).update(is_verified=True)
    return Response({"detail": "Password updated. You can now sign in."})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def set_password(request):
    if not _tenant_access(request.user, request):
        return Response({"detail": "This account cannot access this school."}, status=status.HTTP_403_FORBIDDEN)
    password = str(request.data.get("password", ""))
    try:
        validate_password(password, request.user)
    except Exception as exc:
        return Response({"detail": list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
    request.user.set_password(password)
    request.user.save(update_fields=["password"])
    return Response({"detail": "Password set."})
