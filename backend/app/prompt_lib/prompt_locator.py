# DEPRECATED — re-exports from domains.due_diligence.prompts
# New code should import directly from those packages.
from domains.due_diligence.prompts.analysts import CREATE_ANALYSTS_PROMPT  # noqa: F401
from domains.due_diligence.prompts.interview import (  # noqa: F401
    ANALYST_ASK_QUESTIONS,
    GENERATE_ANSWERS,
    GENERATE_SEARCH_QUERY,
    WRITE_SECTION,
)
from domains.due_diligence.prompts.report import (  # noqa: F401
    INTRO_CONCLUSION_INSTRUCTIONS,
    REPORT_WRITER_INSTRUCTIONS,
)
