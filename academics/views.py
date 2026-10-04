from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from decimal import Decimal, ROUND_HALF_UP
from django.utils.encoding import force_bytes
from django.utils import timezone
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import AcademicCalendarEvent, AcademicYear, AuditEvent, ClassSubjectAssignment, Enrollment, Exam, ExamSubject, GalleryItem, GuardianContact, Mark, Notice, NotificationDelivery, ResultPublicationWindow, RoutineSlot, SchoolProfile, Section, Student, StudentPortalAccess, TeacherProfile
from .permissions import IsSchoolAdmin, IsSchoolMember, membership_for
from .serializers import AcademicYearSerializer, CalendarEventSerializer, ExamSerializer, MarkInputSerializer, NoticeSerializer, RoutineSlotSerializer, SectionSerializer, StudentSerializer
from .services import calculate_result, correct_locked_mark, lock_exam, open_exam, save_mark
from schools.models import LoginIdentity, SchoolMembership
from schools.utils import frontend_base_url


@api_view(["GET"])
@permission_classes([AllowAny])
def public_home(request):
    school = request.tenant
    profile = SchoolProfile.objects.first()
    teachers = TeacherProfile.objects.filter(is_public=True).order_by("display_name")
    return Response({"school": {"name": profile.name if profile else getattr(school, "name", "School"), "slug": getattr(school, "slug", ""), "phone": profile.phone if profile else getattr(school, "phone", ""), "email": profile.email if profile else getattr(school, "email", ""), "address": profile.address if profile else "", "about": profile.about if profile else ""}, "stats": {"students": Student.objects.filter(is_active=True).count(), "teachers": TeacherProfile.objects.filter(is_public=True).count()}, "notices": NoticeSerializer(Notice.objects.filter(is_published=True).order_by("-published_at")[:5], many=True).data, "calendar": CalendarEventSerializer(AcademicCalendarEvent.objects.order_by("starts_on")[:8], many=True).data, "teachers": [{"name": item.display_name, "title": item.title, "bio": item.bio} for item in teachers], "gallery": [{"id": item.pk, "title": item.title, "image_url": item.image_url} for item in GalleryItem.objects.filter(is_published=True).order_by("-created_at")[:8]]})


@api_view(["GET"])
@permission_classes([IsSchoolMember])
def dashboard(request):
    active_year = AcademicYear.objects.filter(is_active=True).first()
    exams = Exam.objects.filter(academic_year=active_year) if active_year else Exam.objects.none()
    membership = membership_for(request)
    open_subjects = ExamSubject.objects.filter(exam__academic_year=active_year, exam__status=Exam.Status.OPEN).select_related("assignment__section") if active_year else ExamSubject.objects.none()
    pending_marks = 0
    for item in open_subjects:
        pending_marks += Enrollment.objects.filter(academic_year=active_year, section=item.assignment.section, status=Enrollment.Status.ACTIVE).exclude(marks__exam_subject=item).count()
    now = timezone.now()
    published_results = ResultPublicationWindow.objects.filter(is_published=True, opens_at__lte=now, closes_at__gte=now).count()
    recent_exams = []
    for exam in exams.prefetch_related("exam_subjects__assignment__section").order_by("-starts_on", "name")[:4]:
        exam_subjects = list(exam.exam_subjects.all())
        total = 0
        entered = 0
        sections = set()
        for item in exam_subjects:
            sections.add(str(item.assignment.section))
            total += Enrollment.objects.filter(academic_year=exam.academic_year, section=item.assignment.section, status=Enrollment.Status.ACTIVE).count()
            entered += item.marks.filter(enrollment__status=Enrollment.Status.ACTIVE).count()
        recent_exams.append({"id": exam.pk, "name": exam.name, "status": exam.status, "starts_on": exam.starts_on, "classes": list(sorted(sections)), "pending_marks": max(total - entered, 0), "progress": round(entered * 100 / total) if total else 0})
    recent_activity = [{"actor": event.actor.get_full_name() or event.actor.get_username() if event.actor else "School team", "action": event.action, "object_type": event.object_type, "object_id": event.object_id, "created_at": event.created_at} for event in AuditEvent.objects.select_related("actor").order_by("-created_at")[:5]]
    upcoming_events = AcademicCalendarEvent.objects.filter(starts_on__gte=timezone.localdate()).order_by("starts_on")[:4]
    return Response({
        "school": request.tenant.name,
        "role": membership.role,
        "active_year": AcademicYearSerializer(active_year).data if active_year else None,
        "students": Student.objects.filter(is_active=True).count(),
        "classes": Section.objects.count(),
        "exams": exams.count(),
        "locked_exams": exams.filter(status=Exam.Status.LOCKED).count(),
        "staff": request.tenant.memberships.filter(is_active=True).count(),
        "pending_marks": pending_marks,
        "published_results": published_results,
        "recent_exams": recent_exams,
        "recent_activity": recent_activity,
        "upcoming_events": CalendarEventSerializer(upcoming_events, many=True).data,
    })


@api_view(["GET", "POST"])
@permission_classes([IsSchoolMember])
@transaction.atomic
def student_list(request):
    if request.method == "GET":
        queryset = Student.objects.filter(is_active=True).prefetch_related("enrollments")
        section_id = request.query_params.get("section")
        if section_id:
            queryset = queryset.filter(enrollments__section_id=section_id, enrollments__status=Enrollment.Status.ACTIVE).distinct()
        return Response(StudentSerializer(queryset.order_by("first_name"), many=True).data)
    if membership_for(request).role != "admin":
        raise PermissionDenied("Only school admins can create student records.")
    data = request.data
    required = ("student_id", "first_name", "academic_year", "section", "roll_number")
    if any(not str(data.get(key, "")).strip() for key in required):
        return Response({"detail": "Student ID, first name, academic year, class section, and roll number are required."}, status=status.HTTP_400_BAD_REQUEST)
    academic_year = get_object_or_404(AcademicYear, pk=data["academic_year"])
    section = get_object_or_404(Section, pk=data["section"])
    if not academic_year.is_active or academic_year.is_archived:
        return Response({"detail": "New students can only be enrolled in the active academic year."}, status=status.HTTP_400_BAD_REQUEST)
    if Student.objects.filter(student_id=data["student_id"].strip()).exists():
        return Response({"detail": "That student ID is already in use at this school."}, status=status.HTTP_409_CONFLICT)
    if (data.get("guardian_email") or data.get("guardian_phone")) and not str(data.get("guardian_name", "")).strip():
        return Response({"detail": "Add the guardian name when providing guardian login details."}, status=status.HTTP_400_BAD_REQUEST)
    student = Student.objects.create(student_id=data["student_id"].strip(), first_name=data["first_name"].strip(), last_name=str(data.get("last_name", "")).strip(), date_of_birth=data.get("date_of_birth") or None)
    Enrollment.objects.create(student=student, academic_year=academic_year, section=section, roll_number=str(data["roll_number"]).strip())
    guardian_name = str(data.get("guardian_name", "")).strip()
    email = str(data.get("guardian_email", "")).strip().lower()
    phone = str(data.get("guardian_phone", "")).strip()
    if guardian_name and (email or phone):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        guardian_users = set()
        if email:
            user = LoginIdentity.objects.filter(kind=LoginIdentity.Kind.EMAIL, value__iexact=email).values_list("user_id", flat=True).first()
            if not user:
                user = User.objects.filter(email__iexact=email).values_list("id", flat=True).first()
            if user:
                guardian_users.add(user)
        if phone:
            user = LoginIdentity.objects.filter(kind=LoginIdentity.Kind.PHONE, value=phone).values_list("user_id", flat=True).first()
            if not user:
                user = User.objects.filter(username=phone).values_list("id", flat=True).first()
            if user:
                guardian_users.add(user)
        if len(guardian_users) > 1:
            raise ValidationError("The guardian email and phone belong to different accounts. Resolve the account links before adding this student.")
        account = User.objects.filter(pk=next(iter(guardian_users))).first() if guardian_users else None
        created = account is None
        if created:
            username = email or phone
            account = User.objects.create_user(username=username, email=email, first_name=guardian_name.split()[0], last_name=" ".join(guardian_name.split()[1:]))
            account.set_unusable_password()
            account.save(update_fields=["password"])
        if email:
            LoginIdentity.objects.get_or_create(user=account, kind=LoginIdentity.Kind.EMAIL, value=email, defaults={"is_verified": False, "is_primary": True})
        if phone:
            LoginIdentity.objects.get_or_create(user=account, kind=LoginIdentity.Kind.PHONE, value=phone, defaults={"is_verified": False, "is_primary": not bool(email)})
        GuardianContact.objects.create(student=student, name=guardian_name, phone=phone, email=email, is_primary=True)
        StudentPortalAccess.objects.get_or_create(user=account, student=student)
        if (created or not account.has_usable_password()) and email:
            uid = urlsafe_base64_encode(force_bytes(account.pk))
            token = default_token_generator.make_token(account)
            link = f"{frontend_base_url(request)}/reset-password?uid={uid}&token={token}"
            send_mail("Your student portal is ready", f"A student profile has been linked to your account. Set a password here: {link}. You can also sign in with a one-time code.", settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
    AuditEvent.objects.create(actor=request.user, action="student.created", object_type="Student", object_id=str(student.pk), after={"student_id": student.student_id, "academic_year": academic_year.name, "section": str(section)})
    return Response(StudentSerializer(student).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsSchoolMember])
def academic_setup(request):
    return Response({
        "years": AcademicYearSerializer(AcademicYear.objects.order_by("-starts_on"), many=True).data,
        "sections": SectionSerializer(Section.objects.select_related("class_level"), many=True).data,
    })


@api_view(["GET"])
@permission_classes([IsSchoolMember])
def exam_list(request):
    exams = Exam.objects.select_related("academic_year", "publication_window").prefetch_related("exam_subjects__assignment__section__class_level", "exam_subjects__assignment__subject").order_by("-starts_on", "name")
    return Response([{
        **ExamSerializer(exam).data,
        "academic_year_name": exam.academic_year.name,
        "results_published": bool(getattr(exam, "publication_window", None) and exam.publication_window.is_published),
        "subjects": [{"id": item.id, "subject": item.assignment.subject.name, "section": str(item.assignment.section), "section_id": item.assignment.section_id, "maximum_marks": str(item.maximum_marks), "pass_marks": str(item.pass_marks), "weight": str(item.weight)} for item in exam.exam_subjects.all()],
    } for exam in exams])


@api_view(["GET"])
@permission_classes([IsSchoolMember])
def result_sheet(request, exam_id):
    exam = get_object_or_404(Exam, pk=exam_id)
    rows = []
    for enrollment in Enrollment.objects.filter(academic_year=exam.academic_year).select_related("student", "section__class_level").order_by("section__class_level__sort_order", "section__name", "roll_number"):
        result = calculate_result(exam, enrollment)
        marks = []
        for item in exam.exam_subjects.filter(assignment__section_id=enrollment.section_id).select_related("assignment__subject").all():
            mark = Mark.objects.filter(exam_subject=item, enrollment=enrollment).first()
            percentage = (mark.score * Decimal("100") / item.maximum_marks).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if mark else None
            marks.append({"exam_subject_id": item.id, "subject": item.assignment.subject.name, "score": str(mark.score) if mark else None, "maximum": str(item.maximum_marks), "percentage": str(percentage) if percentage is not None else None})
        rows.append({"enrollment_id": enrollment.id, "student": StudentSerializer(enrollment.student).data, "roll_number": enrollment.roll_number, "class": str(enrollment.section), "marks": marks, "result": {"percentage": str(result["percentage"]), "grade": result["grade"], "status": result["final_status"]} if result else None})
    return Response({"exam": ExamSerializer(exam).data, "rows": rows})


@api_view(["PUT"])
@permission_classes([IsSchoolMember])
def enter_mark(request, exam_subject_id):
    item = get_object_or_404(ExamSubject.objects.select_related("exam", "assignment__teacher"), pk=exam_subject_id)
    membership = membership_for(request)
    assigned_teacher = item.assignment.teacher.user_id == request.user.id
    if membership.role != "admin" and not assigned_teacher:
        raise PermissionDenied("You are not assigned to this subject.")
    serializer = MarkInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    enrollment = get_object_or_404(Enrollment, pk=serializer.validated_data["enrollment_id"])
    mark = save_mark(exam_subject=item, enrollment=enrollment, score=serializer.validated_data["score"], actor=request.user, reason=serializer.validated_data.get("reason", ""))
    return Response({"id": mark.id, "score": str(mark.score), "updated_at": mark.updated_at})


@api_view(["POST"])
@permission_classes([IsSchoolAdmin])
def exam_lock(request, exam_id):
    exam = get_object_or_404(Exam, pk=exam_id)
    lock_exam(exam=exam, actor=request.user)
    return Response(ExamSerializer(exam).data)


@api_view(["POST"])
@permission_classes([IsSchoolAdmin])
def exam_open(request, exam_id):
    exam = get_object_or_404(Exam, pk=exam_id)
    open_exam(exam=exam, actor=request.user)
    return Response(ExamSerializer(exam).data)


@api_view(["POST"])
@permission_classes([IsSchoolAdmin])
def mark_correction(request, mark_id):
    mark = get_object_or_404(Mark.objects.select_related("exam_subject__exam", "exam_subject__assignment", "enrollment"), pk=mark_id)
    serializer = MarkInputSerializer(data={"enrollment_id": mark.enrollment_id, "score": request.data.get("score"), "reason": request.data.get("reason", "")})
    serializer.is_valid(raise_exception=True)
    if not serializer.validated_data.get("reason", "").strip():
        return Response({"detail": "A correction reason is required."}, status=status.HTTP_400_BAD_REQUEST)
    corrected = correct_locked_mark(mark=mark, score=serializer.validated_data["score"], actor=request.user, reason=serializer.validated_data["reason"])
    return Response({"id": corrected.id, "score": str(corrected.score), "updated_at": corrected.updated_at})


@api_view(["POST"])
@permission_classes([IsSchoolAdmin])
def exam_publish(request, exam_id):
    exam = get_object_or_404(Exam, pk=exam_id)
    if exam.status != Exam.Status.LOCKED:
        return Response({"detail": "Lock and finalize the exam before publishing results."}, status=status.HTTP_409_CONFLICT)
    existing = ResultPublicationWindow.objects.filter(exam=exam).first()
    resend_failed = request.data.get("resend_failed", False)
    if not isinstance(resend_failed, bool):
        return Response({"detail": "resend_failed must be true or false."}, status=status.HTTP_400_BAD_REQUEST)
    if existing and existing.is_published and not resend_failed:
        return Response({"detail": "Results are already published for this exam.", "already_published": True}, status=status.HTTP_409_CONFLICT)
    opens_at = request.data.get("opens_at") or (existing.opens_at if existing and existing.is_published else timezone.now())
    closes_at = request.data.get("closes_at") or (existing.closes_at if existing and existing.is_published else None)
    if not closes_at:
        return Response({"detail": "Provide a result window closing time."}, status=status.HTTP_400_BAD_REQUEST)
    from django.utils.dateparse import parse_datetime
    if isinstance(opens_at, str):
        opens_at = parse_datetime(opens_at)
    if isinstance(closes_at, str):
        closes_at = parse_datetime(closes_at)
    if not opens_at or not closes_at or timezone.is_naive(opens_at) or timezone.is_naive(closes_at):
        return Response({"detail": "Use ISO 8601 timestamps with a timezone."}, status=status.HTTP_400_BAD_REQUEST)
    now = timezone.now()
    if opens_at > now or closes_at <= now or closes_at <= opens_at:
        return Response({"detail": "Results must open now or earlier, and the close time must be in the future."}, status=status.HTTP_400_BAD_REQUEST)
    window, _ = ResultPublicationWindow.objects.update_or_create(exam=exam, defaults={"opens_at": opens_at, "closes_at": closes_at, "is_published": True})
    AuditEvent.objects.create(actor=request.user, action="results.published", object_type="Exam", object_id=str(exam.pk), after={"opens_at": opens_at.isoformat(), "closes_at": closes_at.isoformat()})
    success = 0
    failed = 0
    exam_sections = exam.exam_subjects.values_list("assignment__section_id", flat=True)
    guardian_rows = GuardianContact.objects.filter(student__enrollments__academic_year=exam.academic_year, student__enrollments__section_id__in=exam_sections, student__is_active=True, can_receive_results=True).distinct()
    school_name = getattr(request.tenant, "name", "Your school")
    base_url = frontend_base_url(request)
    message = f"Results for {exam.name} are ready on {school_name}'s secure student portal. Sign in at {base_url}/login to view your student's marksheet."
    for guardian in guardian_rows:
        for channel, destination in ((NotificationDelivery.Channel.EMAIL, guardian.email), (NotificationDelivery.Channel.SMS, guardian.phone)):
            if not destination:
                continue
            previous = NotificationDelivery.objects.filter(exam=exam, guardian=guardian, channel=channel, destination=destination).order_by("-created_at").first()
            if previous and previous.status == NotificationDelivery.Status.SENT:
                continue
            if channel == NotificationDelivery.Channel.EMAIL:
                try:
                    send_mail(f"{exam.name} results are available", message, settings.DEFAULT_FROM_EMAIL, [destination], fail_silently=False)
                    delivery_status, detail = NotificationDelivery.Status.SENT, ""
                except Exception:
                    delivery_status, detail = NotificationDelivery.Status.FAILED, "Email delivery failed. Check the configured email service."
            else:
                sms_backend = getattr(settings, "SMS_BACKEND", "")
                if not sms_backend:
                    delivery_status, detail = NotificationDelivery.Status.UNCONFIGURED, "No SMS_BACKEND is configured."
                else:
                    try:
                        module_name, class_name = sms_backend.rsplit(".", 1)
                        backend = getattr(__import__(module_name, fromlist=[class_name]), class_name)()
                        backend.send(destination, message)
                        delivery_status, detail = NotificationDelivery.Status.SENT, ""
                    except Exception:
                        delivery_status, detail = NotificationDelivery.Status.FAILED, "SMS delivery failed. Check the configured SMS service."
            NotificationDelivery.objects.create(exam=exam, guardian=guardian, channel=channel, destination=destination, status=delivery_status, detail=detail)
            if delivery_status == NotificationDelivery.Status.SENT:
                success += 1
            else:
                failed += 1
    return Response({"exam": exam.name, "opens_at": window.opens_at, "closes_at": window.closes_at, "notifications_sent": success, "notifications_needing_attention": failed, "already_published": bool(existing and existing.is_published)})


@api_view(["GET", "POST"])
@permission_classes([IsSchoolAdmin])
def staff_list(request):
    if request.method == "GET":
        memberships = request.tenant.memberships.select_related("user").order_by("user__last_name", "user__first_name")
        return Response([{"id": row.id, "name": row.user.get_full_name() or row.user.get_username(), "email": row.user.email, "role": row.role, "is_active": row.is_active} for row in memberships])
    email = str(request.data.get("email", "")).strip().lower()
    full_name = str(request.data.get("name", "")).strip()
    role = request.data.get("role", "teacher")
    if not email or "@" not in email or not full_name or role not in ("teacher", "admin"):
        return Response({"detail": "Provide a name, valid email, and teacher/admin role."}, status=status.HTTP_400_BAD_REQUEST)
    User = get_user_model()
    user = User.objects.filter(email__iexact=email).first()
    created = user is None
    if created:
        user = User.objects.create_user(username=email, email=email, first_name=full_name.split()[0], last_name=" ".join(full_name.split()[1:]))
        user.set_unusable_password()
        user.save(update_fields=["password"])
    membership = SchoolMembership.objects.filter(user=user, school=request.tenant).first()
    if membership and membership.is_active:
        return Response({"detail": "This user already has active access to this school."}, status=status.HTTP_409_CONFLICT)
    other = SchoolMembership.objects.filter(user=user, is_active=True).exclude(school=request.tenant).first()
    if other:
        return Response({"detail": "This account already has active access to another school. Revoke that access before adding it here."}, status=status.HTTP_409_CONFLICT)
    if not user.is_active:
        return Response({"detail": "This user account is inactive and cannot be invited."}, status=status.HTTP_409_CONFLICT)
    existing_identity = LoginIdentity.objects.filter(kind=LoginIdentity.Kind.EMAIL, value__iexact=email).first()
    if existing_identity and existing_identity.user_id != user.pk:
        return Response({"detail": "That email is already linked to a different login account."}, status=status.HTTP_409_CONFLICT)
    new_access = not membership or not membership.is_active
    if membership:
        membership.role = role
        membership.is_active = True
        membership.revoked_at = None
        membership.save(update_fields=["role", "is_active", "revoked_at"])
    else:
        membership = SchoolMembership.objects.create(user=user, school=request.tenant, role=role)
    TeacherProfile.objects.update_or_create(user=user, defaults={"display_name": full_name})
    LoginIdentity.objects.get_or_create(user=user, kind=LoginIdentity.Kind.EMAIL, value=email, defaults={"is_verified": False, "is_primary": True})
    AuditEvent.objects.create(actor=request.user, action="staff.access_granted", object_type="SchoolMembership", object_id=str(membership.pk), after={"email": email, "role": role})
    if created or new_access:
        if user.has_usable_password():
            link = f"{frontend_base_url(request)}/login"
            subject = f"You have access to {request.tenant.name}"
            body = f"You have been added to {request.tenant.name} as a {role}. Sign in to your school workspace here: {link}"
        else:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            link = f"{frontend_base_url(request)}/reset-password?uid={uid}&token={token}"
            subject = "Set up your Schoolwise account"
            body = f"You have been added to {request.tenant.name}. Set a password here: {link}"
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
    return Response({"id": membership.id, "name": full_name, "email": email, "role": role, "is_active": True}, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([IsSchoolAdmin])
def staff_role(request, membership_id):
    membership = get_object_or_404(SchoolMembership, pk=membership_id, school=request.tenant)
    role = request.data.get("role")
    if role not in ("teacher", "admin"):
        return Response({"detail": "Role must be teacher or admin."}, status=status.HTTP_400_BAD_REQUEST)
    is_active = request.data.get("is_active", membership.is_active)
    if not isinstance(is_active, bool):
        return Response({"detail": "is_active must be true or false."}, status=status.HTTP_400_BAD_REQUEST)
    removing_admin = membership.role == "admin" and membership.is_active and (role != "admin" or not is_active)
    if removing_admin and request.tenant.memberships.filter(role="admin", is_active=True).count() <= 1:
        return Response({"detail": "A school must keep at least one active administrator."}, status=status.HTTP_409_CONFLICT)
    before = {"role": membership.role, "is_active": membership.is_active}
    membership.role = role
    if "is_active" in request.data:
        membership.is_active = is_active
        membership.revoked_at = None if membership.is_active else timezone.now()
    membership.save(update_fields=["role", "is_active", "revoked_at"])
    AuditEvent.objects.create(actor=request.user, action="staff.role_changed", object_type="SchoolMembership", object_id=str(membership.pk), before=before, after={"role": membership.role, "is_active": membership.is_active})
    return Response({"id": membership.id, "role": membership.role, "is_active": membership.is_active})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portal_students(request):
    students = Student.objects.filter(portal_accesses__user=request.user, is_active=True).distinct()
    return Response(StudentSerializer(students, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portal_student_detail(request, student_id):
    student = get_object_or_404(Student.objects.filter(portal_accesses__user=request.user, is_active=True), pk=student_id)
    slots = RoutineSlot.objects.filter(routine__section__enrollments__student=student, routine__academic_year__is_active=True).select_related("assignment__subject", "assignment__teacher").order_by("weekday", "starts_at")
    available_exam_ids = ResultPublicationWindow.objects.filter(is_published=True, opens_at__lte=timezone.now(), closes_at__gte=timezone.now()).values_list("exam_id", flat=True)
    snapshots = student.enrollments.filter(result_snapshots__exam_id__in=available_exam_ids, result_snapshots__is_current=True).values("result_snapshots__exam__name", "result_snapshots__percentage", "result_snapshots__grade", "result_snapshots__final_status", "result_snapshots__details")
    return Response({"student": StudentSerializer(student).data, "routine": RoutineSlotSerializer(slots, many=True).data, "results": list(snapshots)})
