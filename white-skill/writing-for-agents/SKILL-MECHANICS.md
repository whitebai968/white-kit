# Skill mechanics

The skill-specific branch of [writing-for-agents](SKILL.md): metadata, invocation policy, and routing. Check the target host's current skill-creation guidance before changing these mechanisms.

## Discovery and invocation

Keep the required name and description. Describe the goal and the conditions that make the skill relevant. Preserve the user's existing invocation policy unless they ask to change it.

For Codex, implicit selection is allowed by default. Only when the user requests explicit-only invocation, set the following in the skill's `agents/openai.yaml`, preserving unrelated fields:

```yaml
policy:
  allow_implicit_invocation: false
```

Explicit invocation remains available. These are Codex-specific settings; verify other hosts' supported fields and behavior before adapting a skill. Current reference: [official skill documentation](https://learn.chatgpt.com/docs/build-skills#optional-metadata), checked 2026-09-29.

## Router skills

A router gives a user one entry point for work spanning several skills. State when to use each stage, which instructions to read, what result it should produce, and how that result determines the next stage. Reuse completed work and load only the material needed now.

Links let the executing agent find instructions; available host tools and invocation policy determine how those instructions can be used. A routing document does not itself start background work or create new agents. Respect explicit-invocation requirements and access restrictions when a dependency has them.

## When to separate skills

Separate workflows when their goals, triggering conditions, inputs, or success criteria differ enough to justify independent discovery. Keep shared definitions in one referenced location. Before changing a route, verify the referenced materials exist and that the target host supports the intended way of reaching them.
