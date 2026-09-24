# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]
### Added
- Added `show_review_answers` field to the `Exam` model to control whether students can see correct answers after submission.
- Implemented bulk actions in Django Admin to hide/show review answers for multiple exams at once.
- Imported exam JSON definitions now respect `showReviewAnswers` flag.

### Changed
- **Major Design Update (Tech Green Dark Mode Modern)**:
  - Transitioned UI to a matte-black surface hierarchy with emerald signal accents.
  - Replaced glassy blur cards with framed dashboard cards featuring thin gradient borders and corner brackets.
  - Added technical framing elements: faint grid lines, vertical guides, and mono system labeling.
  - Restrained UI glow effects to active states and localized emphasis.
  - Updated all main templates (`base.html`, `login.html`, `teacher_login.html`, `teacher_dashboard.html`, `teacher_monitor.html`, `components/teacher_nav.html`) to incorporate mono system rail headers, uppercase tracking, and technical design patterns.
