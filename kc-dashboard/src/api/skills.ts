import { apiGet } from "./client";

export type Skill = {
  name: string;
  category: string | null;
  description: string;
  version: string | null;
  platforms: string[] | null;
  tags: string[];
  related_skills: string[];
  skill_dir: string;
};

export type SkillDetail = Skill & {
  body: string;
  supporting_files: {
    references: string[];
    templates: string[];
    assets: string[];
    scripts: string[];
  };
};

export type SkillFile = {
  name: string;
  file_path: string;
  content: string;
};

export const listSkills = () => apiGet<{ skills: Skill[] }>("/skills");

export const getSkill = (name: string) =>
  apiGet<SkillDetail>(`/skills/${encodeURIComponent(name)}`);

export const getSkillFile = (name: string, file_path: string) =>
  apiGet<SkillFile>(
    `/skills/${encodeURIComponent(name)}/files/${file_path
      .split("/")
      .map(encodeURIComponent)
      .join("/")}`,
  );
