# S3Drive — instructions for coding agents

## Project and verification

- Windows S3 drive mounter: Python 3.13, PySide6, PyInstaller, rclone and WinFsp.
- Keep existing portable ZIP layout and `VERSION.txt` contract.
- Do not expose S3 credentials, weaken DPAPI handling, or silently discard VFS writes.
- Run `python -m unittest discover -s tests -v` and syntax checks before reporting a change as verified; the Windows workflow runs the suite three times.
- Check `.github/workflows/build-windows.yml` when changing packaging or application versions.

## Release policy — owner approval required

1. **Never publish a release or create a release Git tag automatically after a push to `main`.** Pushes and pull requests must only test and build artifacts.
2. When completing a meaningful feature, bug fix, or release-ready group of changes, **proactively offer to release a new version** in the response. Choose a proposed `MAJOR.MINOR.PATCH` version using SemVer:
   - patch for backward-compatible fixes;
   - minor for backward-compatible features;
   - major for incompatible changes.
   Give a brief explanation of why that bump fits.
3. **Ask for explicit approval of the exact version** before triggering publication. A proposal alone is not approval; do not create any tag or GitHub Release without the owner's instruction.
4. The first release in this policy is **`v1.0.0`**. Preserve all historic `v0.1.*` tags and releases. Before proposing a subsequent release, check the latest released version to avoid duplicates.
5. Publication uses GitHub Actions → **Build Windows** → **Run workflow**, select branch `main`, enter `release_version` without a `v` prefix (for example `1.0.0`). A successful manual run builds the versioned archive and creates the `vMAJOR.MINOR.PATCH` Git tag and GitHub Release. Never reuse an existing release tag.
6. Ordinary CI artifacts get a `-dev.<github.run_number>` suffix and are not GitHub Releases.
7. If the agent cannot trigger the manual workflow through its connected tools, explain the exact UI steps; never claim that a release has been published before confirming it.
8. **Сообщения всех новых коммитов писать на русском языке** — и заголовок, и содержательную часть. Стандартные префиксы Conventional Commits (feat:, fix:, docs:, test:, ci:, chore:) допустимы, но описание после префикса должно быть по-русски. Писать конкретно, что изменилось и зачем.
9. **Описание каждого GitHub Release формировать из фактических изменений в коммитах с предыдущего релизного SemVer-тега до текущего коммита main**, а не из одинакового шаблона. Текст должен содержать разделы на русском языке (новые возможности, исправления и технические изменения), охватывать только ещё не опубликованные изменения и не приписывать проекту несуществующих возможностей.
10. Перед публикацией по возможности сверять описание с изменениями в diff. GitHub Actions автоматически собирает Markdown release notes из названий коммитов диапазона; агент должен использовать информативные русскоязычные сообщения в новых коммитах. Исторические англоязычные коммиты не переписывать.

These rules also apply when suggesting releases during future work on this repository.
