from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AcademicYear(TimeStamped):
    name = models.CharField(max_length=40)
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_active = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(ends_on__gte=models.F("starts_on")), name="academic_year_end_after_start")]

    def __str__(self):
        return self.name


class SchoolProfile(TimeStamped):
    name = models.CharField(max_length=180)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    about = models.TextField(blank=True)


class ClassLevel(TimeStamped):
    name = models.CharField(max_length=80)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "class"
        verbose_name_plural = "classes"

    def __str__(self):
        return self.name


class Section(TimeStamped):
    class_level = models.ForeignKey(ClassLevel, on_delete=models.CASCADE, related_name="sections")
    name = models.CharField(max_length=40)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["class_level", "name"], name="unique_section_per_class")]
        ordering = ["class_level__sort_order", "name"]

    def __str__(self):
        return f"{self.class_level} · {self.name}"


class Subject(TimeStamped):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=24, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TeacherProfile(TimeStamped):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="teacher_profile")
    display_name = models.CharField(max_length=140)
    title = models.CharField(max_length=80, blank=True)
    bio = models.TextField(blank=True)
    is_public = models.BooleanField(default=False)


class ClassSubjectAssignment(TimeStamped):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="subject_assignments")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="class_assignments")
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.PROTECT, related_name="assignments")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["section", "subject"], name="unique_subject_per_section")]


class Student(TimeStamped):
    student_id = models.CharField(max_length=40, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    photo_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    @property
    def full_name(self):
        return " ".join(part for part in [self.first_name, self.last_name] if part)

    def __str__(self):
        return f"{self.full_name} ({self.student_id})"


class GuardianContact(TimeStamped):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="guardians")
    name = models.CharField(max_length=140)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    relationship = models.CharField(max_length=40, blank=True)
    is_primary = models.BooleanField(default=False)
    can_receive_results = models.BooleanField(default=True)


class StudentPortalAccess(TimeStamped):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="student_portal_accesses")
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="portal_accesses")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "student"], name="unique_student_portal_access")]


class Enrollment(TimeStamped):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        WITHDRAWN = "withdrawn", "Withdrawn"

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="enrollments")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="enrollments")
    section = models.ForeignKey(Section, on_delete=models.PROTECT, related_name="enrollments")
    roll_number = models.CharField(max_length=30)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["student", "academic_year"], name="one_enrollment_per_student_year"),
            models.UniqueConstraint(fields=["section", "academic_year", "roll_number"], name="unique_roll_per_section_year"),
        ]


class Exam(TimeStamped):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open for marks"
        LOCKED = "locked", "Locked"

    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="exams")
    name = models.CharField(max_length=100)
    starts_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="locked_exams")
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-starts_on", "name"]


class ExamSubject(TimeStamped):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="exam_subjects")
    assignment = models.ForeignKey(ClassSubjectAssignment, on_delete=models.PROTECT, related_name="exam_subjects")
    maximum_marks = models.DecimalField(max_digits=7, decimal_places=2, validators=[MinValueValidator(0.01)])
    pass_marks = models.DecimalField(max_digits=7, decimal_places=2, validators=[MinValueValidator(0)])
    weight = models.DecimalField(max_digits=6, decimal_places=3, default=1, validators=[MinValueValidator(0.001)])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["exam", "assignment"], name="unique_exam_subject_assignment"),
            models.CheckConstraint(condition=models.Q(pass_marks__lte=models.F("maximum_marks")), name="exam_subject_pass_within_max"),
            models.CheckConstraint(condition=models.Q(weight__gt=0), name="exam_subject_weight_positive"),
        ]


class Mark(TimeStamped):
    exam_subject = models.ForeignKey(ExamSubject, on_delete=models.CASCADE, related_name="marks")
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="marks")
    score = models.DecimalField(max_digits=7, decimal_places=2, validators=[MinValueValidator(0)])
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="entered_marks")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["exam_subject", "enrollment"], name="unique_mark_per_exam_subject_student")]


class GradeScale(TimeStamped):
    name = models.CharField(max_length=100, default="Default")
    academic_year = models.ForeignKey(AcademicYear, null=True, blank=True, on_delete=models.SET_NULL, related_name="grade_scales")
    is_default = models.BooleanField(default=False)


class GradeBand(TimeStamped):
    scale = models.ForeignKey(GradeScale, on_delete=models.CASCADE, related_name="bands")
    label = models.CharField(max_length=16)
    minimum_percentage = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(0), MaxValueValidator(100)])
    maximum_percentage = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(0), MaxValueValidator(100)])
    grade_point = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["-minimum_percentage"]


class ResultPublicationWindow(TimeStamped):
    exam = models.OneToOneField(Exam, on_delete=models.CASCADE, related_name="publication_window")
    opens_at = models.DateTimeField()
    closes_at = models.DateTimeField()
    is_published = models.BooleanField(default=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(closes_at__gt=models.F("opens_at")), name="result_window_close_after_open")]


class NotificationDelivery(models.Model):
    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        UNCONFIGURED = "unconfigured", "Unconfigured"

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="notification_deliveries")
    guardian = models.ForeignKey(GuardianContact, on_delete=models.CASCADE, related_name="notification_deliveries")
    channel = models.CharField(max_length=8, choices=Channel.choices)
    destination = models.CharField(max_length=254)
    status = models.CharField(max_length=16, choices=Status.choices)
    detail = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ClassRoutine(TimeStamped):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="routines")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="routines")
    name = models.CharField(max_length=100, default="Regular routine")


class RoutineSlot(TimeStamped):
    class Weekday(models.IntegerChoices):
        SUNDAY = 0, "Sunday"
        MONDAY = 1, "Monday"
        TUESDAY = 2, "Tuesday"
        WEDNESDAY = 3, "Wednesday"
        THURSDAY = 4, "Thursday"
        FRIDAY = 5, "Friday"
        SATURDAY = 6, "Saturday"

    routine = models.ForeignKey(ClassRoutine, on_delete=models.CASCADE, related_name="slots")
    assignment = models.ForeignKey(ClassSubjectAssignment, on_delete=models.PROTECT, related_name="routine_slots")
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    starts_at = models.TimeField()
    ends_at = models.TimeField()
    room = models.CharField(max_length=40, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(ends_at__gt=models.F("starts_at")), name="routine_slot_end_after_start")]


class ResultSnapshot(TimeStamped):
    exam = models.ForeignKey(Exam, on_delete=models.PROTECT, related_name="snapshots")
    enrollment = models.ForeignKey(Enrollment, on_delete=models.PROTECT, related_name="result_snapshots")
    percentage = models.DecimalField(max_digits=6, decimal_places=2)
    grade = models.CharField(max_length=16)
    final_status = models.CharField(max_length=16)
    details = models.JSONField(default=dict)
    version = models.PositiveSmallIntegerField(default=1)
    is_current = models.BooleanField(default=True)
    calculated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["exam", "enrollment", "version"], name="unique_result_snapshot_version")]


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="school_audit_events")
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80)
    reason = models.TextField(blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Notice(TimeStamped):
    title = models.CharField(max_length=180)
    body = models.TextField()
    published_at = models.DateTimeField(null=True, blank=True)
    is_published = models.BooleanField(default=False)


class GalleryItem(TimeStamped):
    title = models.CharField(max_length=140, blank=True)
    image_url = models.URLField()
    is_published = models.BooleanField(default=False)


class AcademicCalendarEvent(TimeStamped):
    title = models.CharField(max_length=160)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
