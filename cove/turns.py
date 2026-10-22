# Messages in the order every chat template takes. Some templates (Mistral Nemo and its
# fine-tunes, Gutenberg among them) refuse anything but one system message first and then user
# and assistant strictly in turn: "conversation roles must alternate". So before a request goes
# out, extra system messages join the first one, messages of the same side that follow each
# other become one, and a talk that would open with the model's turn gets a short user line in
# front. Tool calls and their results stay as they are.

def text_parts(content):
    return content if isinstance(content, list) else [{"type": "text", "text": content or ""}]


def joined(a, b):
    """Two contents as one: plain text stays text; with pictures, a list of parts."""
    if isinstance(a, str) and isinstance(b, str):
        return f"{a}\n\n{b}" if a and b else a or b
    return text_parts(a) + text_parts(b)


def in_turns(messages):
    systems = [m for m in messages if m.get("role") == "system"]
    out = []
    if systems:
        first = dict(systems[0])
        for m in systems[1:]:
            first["content"] = joined(first.get("content", ""), m.get("content", ""))
        out.append(first)
    for m in messages:
        if m.get("role") == "system":
            continue
        last = out[-1] if out else None
        plain = m.get("role") in ("user", "assistant") and not m.get("tool_calls")
        if last and plain and last.get("role") == m["role"] and not last.get("tool_calls"):
            out[-1] = {**last, "content": joined(last.get("content", ""), m.get("content", ""))}
        else:
            out.append(dict(m))
    start = 1 if systems else 0
    if len(out) > start and out[start].get("role") == "assistant":
        out.insert(start, {"role": "user", "content": "(Our talk so far.)"})
    return out


def body_in_turns(body):
    """The request body with its messages put in turns (the body itself is not changed)."""
    if isinstance(body, dict) and isinstance(body.get("messages"), list):
        return {**body, "messages": in_turns(body["messages"])}
    return body
