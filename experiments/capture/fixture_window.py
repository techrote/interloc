from __future__ import annotations

import tkinter as tk

TITLE = "Interloc IL-012 Fixture"
WIDTH = 640
HEIGHT = 360


def main() -> None:
    root = tk.Tk()
    root.title(TITLE)
    root.geometry(f"{WIDTH}x{HEIGHT}")
    root.minsize(WIDTH, HEIGHT)

    canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    # Large, high-contrast geometry makes wrong/blank/stale captures obvious
    # without relying on OCR or platform font rendering.
    canvas.create_rectangle(0, 0, WIDTH // 2, HEIGHT // 2, fill="#d32f2f", outline="")
    canvas.create_rectangle(WIDTH // 2, 0, WIDTH, HEIGHT // 2, fill="#1976d2", outline="")
    canvas.create_rectangle(0, HEIGHT // 2, WIDTH // 2, HEIGHT, fill="#388e3c", outline="")
    canvas.create_rectangle(WIDTH // 2, HEIGHT // 2, WIDTH, HEIGHT, fill="#fbc02d", outline="")
    canvas.create_oval(250, 110, 390, 250, fill="#111111", outline="#ffffff", width=6)
    canvas.create_line(40, 40, WIDTH - 40, HEIGHT - 40, fill="#ffffff", width=8)
    canvas.create_line(WIDTH - 40, 40, 40, HEIGHT - 40, fill="#000000", width=8)

    # A changing marker lets an operator detect a stale frame. It is not used as
    # a deterministic whole-image hash because window chrome/DPI can legitimately
    # vary across hosts.
    sequence = tk.StringVar(value="SEQ 000000")
    label = tk.Label(
        root,
        textvariable=sequence,
        font=("Consolas", 18, "bold"),
        fg="white",
        bg="black",
        padx=8,
        pady=4,
    )
    label.place(relx=0.5, rely=0.5, anchor="center")

    counter = 0

    def tick() -> None:
        nonlocal counter
        counter += 1
        sequence.set(f"SEQ {counter:06d}")
        root.after(500, tick)

    root.after(500, tick)
    root.mainloop()


if __name__ == "__main__":
    main()
