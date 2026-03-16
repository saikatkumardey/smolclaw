# HEARTBEAT.md

This file tells you what to check and when to speak up during a heartbeat.

## When to send a message

Reach out if any of these are true:

- A background task you started has finished or failed
- Something you were monitoring has changed
- A deadline or reminder the user asked you to track is approaching
- You hit an error on a previous task that needs user input
- It's been a while and the user asked for a periodic update on something

## When to stay silent

Stay silent (reply `HEARTBEAT_OK` only) if:

- Nothing has changed since the last heartbeat
- The user is likely asleep (check timezone in USER.md, skip 23:00–08:00 local time)
- You have nothing new to say — don't repeat yourself

## How to decide

1. Check MEMORY.md for any pending tasks or open threads
2. Check the user's timezone in USER.md — if it's night, stay silent
3. If something is worth saying, send one short message via `telegram_send`, then reply `HEARTBEAT_OK`
4. If nothing is worth saying, reply `HEARTBEAT_OK` only

## Important

- Do NOT run bash commands or scripts during heartbeat unless absolutely necessary
- Do NOT grep logs, check external services, or run multi-step investigations
- If you spot something that needs investigation, note it for the subconscious or spawn_task — don't do it inline
- The heartbeat should complete in under 30 seconds. Read files, decide, respond.

## Format

Keep heartbeat messages short. One or two sentences. No preamble.
