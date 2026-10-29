# from app.models.admin import Admin
# from app.models.school import School
# from app.models.school_admin import SchoolAdmin
# from app.models.class_ import Class
# from app.models.subject import Subject
# from app.models.teacher import Teacher
# from app.models.student import Student
# from app.models.recording import Recording, RecordingStatus
# from app.models.llm_report import LLMReport
# from app.models.chapter import Chapter
# from app.models.chapter_document import ChapterDocument
# from app.models.chapter_chunk import ChapterChunk
# from app.models.chapter_knowledge import ChapterKnowledge

# __all__ = [
#     "Admin", "School", "SchoolAdmin", "Class", "Subject",
#     "Teacher", "Student", "Recording", "RecordingStatus", "LLMReport",
#     "Chapter", "ChapterDocument", "ChapterChunk", "ChapterKnowledge",
# ]


from app.models.admin import Admin
from app.models.school import School
from app.models.school_admin import SchoolAdmin
from app.models.class_ import Class
from app.models.subject import Subject
from app.models.teacher import Teacher
from app.models.student import Student
from app.models.recording import Recording, RecordingStatus
from app.models.llm_report import LLMReport
from app.models.chapter import Chapter
from app.models.chapter_document import ChapterDocument
from app.models.chapter_chunk import ChapterChunk
from app.models.chapter_knowledge import ChapterKnowledge
from app.models.timetable_entry import TimetableEntry
from app.models.learning_plan import LearningPlan
from app.models.auth_token import AuthToken
from app.models.email_log import EmailLog, EmailStatus
from app.models.system_setting import SystemSetting
from app.models.audit_log import AuditLog
from app.models.report_feedback import ReportFeedback, FeedbackTarget, FeedbackVerdict
from app.models.self_rating import SelfRating
from app.models.class_brief_mute import ClassBriefMute

__all__ = [
    "Admin", "School", "SchoolAdmin", "Class", "Subject",
    "Teacher", "Student", "Recording", "RecordingStatus", "LLMReport",
    "Chapter", "ChapterDocument", "ChapterChunk", "ChapterKnowledge",
    "TimetableEntry", "LearningPlan", "AuthToken", "EmailLog", "EmailStatus",
    "SystemSetting", "AuditLog", "ReportFeedback", "FeedbackTarget", "FeedbackVerdict", "SelfRating",
    "ClassBriefMute",
]
