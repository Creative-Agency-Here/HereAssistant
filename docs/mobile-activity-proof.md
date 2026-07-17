# Verified mobile assistant activity

The Activity screen exposes what the assistant actually called and what happened next, without turning the main conversation into a wall of logs. Consecutive actions stay collapsed in one bottom sheet; tapping an item expands its structured detail. The sheet can also be pulled up, collapsed, or dismissed with a downward gesture.

These screenshots were captured at a 390 × 844 mobile viewport from the generated Nuxt application. Before the list screenshot, the browser performs a real upward drag and asserts that the sheet expanded. The same fixture is used by automated renderer tests. It contains synthetic, non-secret data.

## Action list

![Five assistant action modes in the mobile bottom sheet](img/activity/activity-actions.png)

## Read and Edit

<table>
  <tr>
    <th>Read: path, line range and returned content</th>
    <th>Edit: path, visual diff and result</th>
  </tr>
  <tr>
    <td><img src="img/activity/activity-read.png" alt="Read action detail" width="390"></td>
    <td><img src="img/activity/activity-edit.png" alt="Edit action detail" width="390"></td>
  </tr>
</table>

## Write and Bash

<table>
  <tr>
    <th>Write: path, written content and result</th>
    <th>Bash: exact command, cwd, output, exit code and duration</th>
  </tr>
  <tr>
    <td><img src="img/activity/activity-write.png" alt="Write action detail" width="390"></td>
    <td><img src="img/activity/activity-bash.png" alt="Bash action detail" width="390"></td>
  </tr>
</table>

## Agent

![Agent task, result, duration and token usage](img/activity/activity-agent.png)

## Verified contract

| Mode | Stored and rendered fields |
|---|---|
| Read | file path, line range, bounded content, status and duration |
| Edit | file path, bounded before/after values, visual diff, result and status |
| Write | file path, bounded content preview, result and status |
| Bash | exact bounded command, cwd, output, exit code, status and duration |
| Agent | agent name, task, result, status, duration and token counts when available |

The sync parser uses an explicit allowlist, size limits and secret redaction before data reaches HereCRM. Unknown input properties are discarded. Private projects still remain private: this richer payload is sent only when the project policy explicitly allows CRM session sync.

Reproducible checks:

```bash
cd webapp/front
npm run test:activity
npm run generate
cd ../..
python -m pytest tests/tooling/test_mobile_activity_proof.py
```

Russian version: [mobile-activity-proof.ru.md](mobile-activity-proof.ru.md).
