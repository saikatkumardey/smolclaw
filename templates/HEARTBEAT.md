# HEARTBEAT.md

Every 30 minutes, you get a heartbeat check. This file tells you what to do with it.

## Rules

**Reach out when:**
- A task you were working on has an update worth sharing
- You notice something broken, blocked, or overdue
- There's a pending reminder the user set up
- You have a question that would unblock real work

**Stay quiet when:**
- Nothing has changed since the last check
- It's late (use timezone from USER.md — don't message between 23:00 and 08:00)
- You'd just be repeating yourself
- You have nothing specific to say

## What to check

Add your own checks below. These run every heartbeat.

<!-- Examples:
- Check for any cron jobs that failed recently
- Remind about any tasks marked as pending in MEMORY.md
- Check if any skills or tools need updating
-->

## How to respond

If you have something worth saying: use `telegram_send` to message the user directly. Keep it short — one or two sentences. Then reply `HEARTBEAT_OK`.

If you have nothing: reply with just `HEARTBEAT_OK`. No message, no explanation.

`HEARTBEAT_OK` is the only acceptable silent response. Anything else gets sent to the user.
