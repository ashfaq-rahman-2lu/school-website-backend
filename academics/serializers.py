from rest_framework import serializers

from .models import AcademicYear, AcademicCalendarEvent, Enrollment, Exam, Notice, RoutineSlot, Section, Student


class AcademicYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicYear
        fields = ("id", "name", "starts_on", "ends_on", "is_active", "is_archived")


class SectionSerializer(serializers.ModelSerializer):
    class_name = serializers.CharField(source="class_level.name", read_only=True)

    class Meta:
        model = Section
        fields = ("id", "name", "class_level", "class_name")


class StudentSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    roll_number = serializers.SerializerMethodField()
    class_name = serializers.SerializerMethodField()
    section_name = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = ("id", "student_id", "first_name", "last_name", "full_name", "date_of_birth", "is_active", "roll_number", "class_name", "section_name")

    def get_roll_number(self, obj):
        enrollment = self._enrollment(obj)
        return enrollment.roll_number if enrollment else ""

    def get_class_name(self, obj):
        enrollment = self._enrollment(obj)
        return enrollment.section.class_level.name if enrollment else ""

    def get_section_name(self, obj):
        enrollment = self._enrollment(obj)
        return enrollment.section.name if enrollment else ""

    def _enrollment(self, obj):
        return obj.enrollments.filter(status=Enrollment.Status.ACTIVE, academic_year__is_active=True).select_related("section__class_level").first()


class ExamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Exam
        fields = ("id", "academic_year", "name", "starts_on", "status", "locked_at")


class MarkInputSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    score = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=0)
    reason = serializers.CharField(required=False, allow_blank=True)


class NoticeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notice
        fields = ("id", "title", "body", "published_at")


class CalendarEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicCalendarEvent
        fields = ("id", "title", "starts_on", "ends_on", "description")


class RoutineSlotSerializer(serializers.ModelSerializer):
    subject = serializers.CharField(source="assignment.subject.name", read_only=True)
    teacher = serializers.CharField(source="assignment.teacher.display_name", read_only=True)

    class Meta:
        model = RoutineSlot
        fields = ("id", "weekday", "starts_at", "ends_at", "room", "subject", "teacher")
