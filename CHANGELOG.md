# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [2.2] - 2026-10-03

### Fixed
- Fixed rejected AJAX requests caused by reading an HttpOnly CSRF cookie. Focus violations, audit reports, and game actions now use Django's token rendered on the current exam page.
- Surface failed focus reports instead of silently dropping them. Restore popup warnings when students return to a visible, focused exam, with a persistent on-page warning as backup.
- Fix premature review expiration from reused question timestamps, final-question timeout loops, and acceptance of answers after review time expires.
- Move review questions marked "Leave for Later" to the back of a persistent queue while preserving original question numbers.
- Check server expiration immediately when the countdown reaches zero. Prevent duplicate submissions and stale heartbeat responses from overriding a question transition.
- Stop updating completed submissions' heartbeats and displaying an increasing last-seen counter after completion.

### Changed
- Display scores as correct / total questions, with answered and unanswered counts separately.
- Show active students' last-seen times in minutes and seconds; finished students show "Done".
- Count window blur during question saves and add a focus-state fallback for missed mobile blur events.
- Require confirmed fullscreen before student login submission. Hide questions and block exam interaction outside fullscreen while the timer continues running.
- Keep CSRF protection enabled. Fullscreen and focus checks remain browser controls and cannot prevent operating-system app switching.

### Added
- Migration `0010_submission_review_order` for the persistent review queue. Run `python manage.py migrate` before using this version.
- Regression coverage for CSRF-enforced reporting and escalation, skip/review flows, completed-student monitoring, focus warnings, fullscreen gates, and timer/submission races.

### Validation
- 33 Django tests and 16 JavaScript tests passed.
- Verified the submissions, answers, and violations admin pages render after applying the migration to the configured database.

## [2.1]

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
