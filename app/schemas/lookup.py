from pydantic import BaseModel


class TeacherLookupOut(BaseModel):
    exists: bool
    id: int | None = None
    name: str | None = None
    email: str | None = None
    
    
class ClassLookupOut(BaseModel):
    exists: bool
    id: int | None = None
    name: str
    section: str
    

class SubjectLookupOut(BaseModel):
    exists: bool
    id: int | None = None
    name: str
    class_id: int