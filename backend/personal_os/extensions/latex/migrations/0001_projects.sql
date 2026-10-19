-- Per-project compile settings. A project is a Files folder, so it is keyed by the Grain project ('' is Personal) and the folder path.
CREATE TABLE IF NOT EXISTS latex_projects (
  scope TEXT NOT NULL,
  folder TEXT NOT NULL,
  engine TEXT NOT NULL DEFAULT 'pdflatex',
  PRIMARY KEY (scope, folder)
);
