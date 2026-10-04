from django.urls import path

from . import auth_views

urlpatterns = [
    path("csrf/", auth_views.csrf, name="auth-csrf"),
    path("login/", auth_views.password_login, name="auth-login"),
    path("otp/start/", auth_views.otp_start, name="auth-otp-start"),
    path("otp/verify/", auth_views.otp_verify, name="auth-otp-verify"),
    path("logout/", auth_views.logout_user, name="auth-logout"),
    path("me/", auth_views.current_user, name="auth-me"),
    path("password/reset/", auth_views.password_reset_request, name="password-reset-request"),
    path("password/reset/confirm/", auth_views.password_reset_confirm, name="password-reset-confirm"),
    path("password/set/", auth_views.set_password, name="password-set"),
]
