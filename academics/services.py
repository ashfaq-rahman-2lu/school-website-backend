from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import AuditEvent, Enrollment, Exam, Mark, ResultSnapshot


def _percentage(score, maximum):
    return (score * Decimal("100") / maximum).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_result(exam, enrollment):
    exam_subjects = list(exam.exam_subjects.filter(assignment__section_id=enrollment.section_id).select_related("assignment__subject").all())
    if not exam_subjects:
        return None
    numerator = Decimal("0")
    denominator = Decimal("0")
    details = []
    failed = False
    for item in exam_subjects:
        mark = Mark.objects.filter(exam_subject=item, enrollment=enrollment).first()
        if mark is None:
            return None
        score = mark.score
        percentage = _percentage(score, item.maximum_marks)
        failed = failed or score < item.pass_marks
        numerator += percentage * item.weight
        denominator += item.weight
        details.append({"subject": item.assignment.subject.name, "score": str(score), "maximum": str(item.maximum_marks), "percentage": str(percentage), "passed": score >= item.pass_marks})
    overall = (numerator / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if denominator else Decimal("0")
    from .models import GradeBand, GradeScale
    scale = GradeScale.objects.filter(academic_year=exam.academic_year).order_by("-is_default").first() or GradeScale.objects.filter(is_default=True).first()
    grade = "—"
    if scale:
        band = GradeBand.objects.filter(scale=scale, minimum_percentage__lte=overall).filter(Q(maximum_percentage__gt=overall) | Q(maximum_percentage=100, minimum_percentage__lte=overall)).first()
        grade = band.label if band else "—"
    return {"percentage": overall, "grade": "F" if failed else grade, "final_status": "fail" if failed else "pass", "details": details}


@transaction.atomic
def save_mark(*, exam_subject, enrollment, score, actor, reason=""):
    exam = exam_subject.exam
    if exam.status == Exam.Status.LOCKED:
        raise ValidationError("This exam is locked. Ask an administrator to authorize a correction.")
    if exam.status != Exam.Status.OPEN:
        raise ValidationError("This exam is not open for marks entry.")
    if enrollment.academic_year_id != exam.academic_year_id:
        raise ValidationError("Student enrollment and exam must belong to the same academic year.")
    if not enrollment.section_id == exam_subject.assignment.section_id:
        raise ValidationError("This student is not enrolled in the class for this subject.")
    if score > exam_subject.maximum_marks:
        raise ValidationError({"score": f"Score cannot exceed {exam_subject.maximum_marks}."})
    mark = Mark.objects.filter(exam_subject=exam_subject, enrollment=enrollment).first()
    before = {"score": str(mark.score)} if mark else None
    if mark:
        mark.score = score
        mark.entered_by = actor
        mark.save(update_fields=["score", "entered_by", "updated_at"])
        action = "mark.updated"
    else:
        mark = Mark.objects.create(exam_subject=exam_subject, enrollment=enrollment, score=score, entered_by=actor)
        action = "mark.created"
    AuditEvent.objects.create(actor=actor, action=action, object_type="Mark", object_id=str(mark.pk), reason=reason, before=before, after={"score": str(mark.score)})
    return mark


@transaction.atomic
def lock_exam(*, exam, actor):
    if exam.status == Exam.Status.LOCKED:
        return exam
    exam_subjects = list(exam.exam_subjects.select_related("assignment__section").all())
    if not exam_subjects:
        raise ValidationError("Add at least one class subject before locking this exam.")
    for exam_subject in exam_subjects:
        enrollments = Enrollment.objects.filter(academic_year=exam.academic_year, section=exam_subject.assignment.section, status=Enrollment.Status.ACTIVE)
        missing = enrollments.exclude(marks__exam_subject=exam_subject)
        if missing.exists():
            raise ValidationError(f"Cannot lock yet: {missing.count()} student mark(s) are missing for {exam_subject.assignment.subject.name}.")
    exam.status = Exam.Status.LOCKED
    exam.locked_by = actor
    exam.locked_at = timezone.now()
    exam.save(update_fields=["status", "locked_by", "locked_at", "updated_at"])
    for enrollment in Enrollment.objects.filter(academic_year=exam.academic_year, status=Enrollment.Status.ACTIVE):
        result = calculate_result(exam, enrollment)
        if result:
            ResultSnapshot.objects.update_or_create(exam=exam, enrollment=enrollment, version=1, defaults={**result, "is_current": True})
    AuditEvent.objects.create(actor=actor, action="exam.locked", object_type="Exam", object_id=str(exam.pk), after={"status": exam.status, "locked_at": exam.locked_at.isoformat()})
    return exam


@transaction.atomic
def open_exam(*, exam, actor):
    if exam.status == Exam.Status.LOCKED:
        raise ValidationError("A locked exam cannot be reopened from this action.")
    if exam.status == Exam.Status.OPEN:
        return exam
    exam.status = Exam.Status.OPEN
    exam.save(update_fields=["status", "updated_at"])
    AuditEvent.objects.create(actor=actor, action="exam.opened", object_type="Exam", object_id=str(exam.pk), after={"status": exam.status})
    return exam


@transaction.atomic
def correct_locked_mark(*, mark, score, actor, reason):
    if mark.exam_subject.exam.status != Exam.Status.LOCKED:
        raise ValidationError("Use ordinary marks entry while the exam is open.")
    if not reason.strip():
        raise ValidationError({"reason": "An explanation is required for a post-lock correction."})
    if score < 0 or score > mark.exam_subject.maximum_marks:
        raise ValidationError({"score": f"Score must be between 0 and {mark.exam_subject.maximum_marks}."})
    before = {"score": str(mark.score)}
    mark.score = score
    mark.entered_by = actor
    mark.save(update_fields=["score", "entered_by", "updated_at"])
    AuditEvent.objects.create(actor=actor, action="mark.corrected_after_lock", object_type="Mark", object_id=str(mark.pk), reason=reason, before=before, after={"score": str(mark.score)})
    result = calculate_result(mark.exam_subject.exam, mark.enrollment)
    if result:
        current = ResultSnapshot.objects.filter(exam=mark.exam_subject.exam, enrollment=mark.enrollment, is_current=True).first()
        version = current.version + 1 if current else 1
        if current:
            current.is_current = False
            current.save(update_fields=["is_current", "updated_at"])
        ResultSnapshot.objects.create(exam=mark.exam_subject.exam, enrollment=mark.enrollment, version=version, **result)
    return mark
