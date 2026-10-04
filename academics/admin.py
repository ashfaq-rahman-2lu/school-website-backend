from django.contrib import admin

from .models import (
    AcademicCalendarEvent, AcademicYear, AuditEvent, ClassLevel, ClassRoutine,
    ClassSubjectAssignment, Enrollment, Exam, ExamSubject, GalleryItem,
    GradeBand, GradeScale, GuardianContact, Mark, Notice, NotificationDelivery, ResultPublicationWindow,
    RoutineSlot, SchoolProfile, Section, Student, StudentPortalAccess, Subject,
    TeacherProfile,
)


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "starts_on", "status", "locked_by", "locked_at")
    list_filter = ("status", "academic_year")
    search_fields = ("name",)
    readonly_fields = ("locked_by", "locked_at")


@admin.register(Mark)
class MarkAdmin(admin.ModelAdmin):
    list_display = ("enrollment", "exam_subject", "score", "entered_by", "updated_at")
    list_filter = ("exam_subject__exam", "exam_subject__assignment__section")
    search_fields = ("enrollment__student__first_name", "enrollment__student__student_id")
    readonly_fields = ("entered_by", "created_at", "updated_at")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "object_type", "object_id")
    list_filter = ("action", "object_type")
    readonly_fields = ("actor", "action", "object_type", "object_id", "reason", "before", "after", "created_at")


for model in (
    AcademicCalendarEvent, AcademicYear, ClassLevel, ClassRoutine, ClassSubjectAssignment,
    Enrollment, ExamSubject, GalleryItem, GradeBand, GradeScale, GuardianContact,
    Notice, ResultPublicationWindow, RoutineSlot, SchoolProfile, Section, Student,
    StudentPortalAccess, Subject, TeacherProfile,
    NotificationDelivery,
):
    admin.site.register(model)
