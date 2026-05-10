"""kc-skills public exports."""

__version__ = "0.1.0"

from kc_skills.frontmatter import (
    FrontmatterError,
    parse_skill_frontmatter,
    skill_matches_platform,
)
from kc_skills.skill_index import (
    PathOutsideSkillDir,
    Skill,
    SkillIndex,
    SkillSummary,
)
from kc_skills.tools import build_skill_tools

__all__ = [
    "FrontmatterError",
    "parse_skill_frontmatter",
    "skill_matches_platform",
    "PathOutsideSkillDir",
    "Skill",
    "SkillIndex",
    "SkillSummary",
    "build_skill_tools",
]
