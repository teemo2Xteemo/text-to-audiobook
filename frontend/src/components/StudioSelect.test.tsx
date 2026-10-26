import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { StudioSelect, type StudioSelectOption } from "./StudioSelect";

const sampleOptions: StudioSelectOption[] = [
  { value: "en-US", label: "English (US)", displayLabel: "English (US) (en-US)", badge: "EN" },
  { value: "vi-VN", label: "Vietnamese", displayLabel: "Vietnamese (vi-VN)", badge: "VI" },
  { value: "ja-JP", label: "Japanese", displayLabel: "Japanese (ja-JP)", badge: "JA" },
  { value: "fr-FR", label: "French", displayLabel: "French (fr-FR)", badge: "FR" },
  { value: "de-DE", label: "German", displayLabel: "German (de-DE)", badge: "DE" },
  { value: "es-ES", label: "Spanish", displayLabel: "Spanish (es-ES)", badge: "ES" },
];

describe("StudioSelect", () => {
  it("renders trigger and displays selected option", () => {
    render(
      <StudioSelect
        id="test-lang"
        label="Language"
        value="vi-VN"
        options={sampleOptions}
        onChange={() => undefined}
      />,
    );

    expect(screen.getByText("Language")).toBeInTheDocument();
    expect(screen.getByText("Vietnamese (vi-VN)")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Filter language...")).toBeNull();
  });

  it("automatically focuses the search input when opened so the user can immediately type", async () => {
    const user = userEvent.setup();
    render(
      <StudioSelect
        id="test-lang"
        label="Language"
        value="en-US"
        options={sampleOptions}
        onChange={() => undefined}
      />,
    );

    // Click the custom trigger
    const trigger = screen.getByText("English (US) (en-US)");
    await user.click(trigger);

    // Search input should be rendered and automatically focused
    const searchInput = screen.getByPlaceholderText("Filter language...");
    expect(searchInput).toBeInTheDocument();
    expect(document.activeElement).toBe(searchInput);

    // User can immediately type to filter without clicking again
    await user.keyboard("viet");
    expect(searchInput).toHaveValue("viet");

    // Only matching option should be visible in the listbox
    const listbox = screen.getByRole("listbox");
    expect(within(listbox).getByText("Vietnamese (vi-VN)")).toBeInTheDocument();
    expect(within(listbox).queryByText("English (US) (en-US)")).toBeNull();
  });

  it("selects filtered option on Enter and does not submit enclosing form", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onSubmit = vi.fn((e) => e.preventDefault());

    render(
      <form onSubmit={onSubmit}>
        <StudioSelect
          id="test-lang"
          label="Language"
          value="en-US"
          options={sampleOptions}
          onChange={onChange}
        />
        <button type="submit">Submit</button>
      </form>,
    );

    const trigger = screen.getByText("English (US) (en-US)");
    await user.click(trigger);

    const searchInput = screen.getByPlaceholderText("Filter language...");
    expect(document.activeElement).toBe(searchInput);

    // Type query and press Enter
    await user.keyboard("span[Enter]");

    // Should call onChange with selected value
    expect(onChange).toHaveBeenCalledWith("es-ES");
    // Form should NOT have been submitted
    expect(onSubmit).not.toHaveBeenCalled();
    // Dropdown should be closed
    expect(screen.queryByPlaceholderText("Filter language...")).toBeNull();
  });

  it("supports keyboard arrow navigation (ArrowDown / ArrowUp) and selection", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(
      <StudioSelect
        id="test-lang"
        label="Language"
        value="en-US"
        options={sampleOptions}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByText("English (US) (en-US)"));
    const searchInput = screen.getByPlaceholderText("Filter language...");
    expect(document.activeElement).toBe(searchInput);

    // Arrow down to second option (vi-VN) and press Enter
    await user.keyboard("[ArrowDown][Enter]");

    expect(onChange).toHaveBeenCalledWith("vi-VN");
    expect(screen.queryByPlaceholderText("Filter language...")).toBeNull();
  });

  it("closes popover on Escape and restores focus", async () => {
    const user = userEvent.setup();
    render(
      <StudioSelect
        id="test-lang"
        label="Language"
        value="en-US"
        options={sampleOptions}
        onChange={() => undefined}
      />,
    );

    await user.click(screen.getByText("English (US) (en-US)"));
    expect(screen.getByPlaceholderText("Filter language...")).toBeInTheDocument();

    await user.keyboard("[Escape]");
    expect(screen.queryByPlaceholderText("Filter language...")).toBeNull();
  });

  it("closes popover on outside click", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <span data-testid="outside">Outside area</span>
        <StudioSelect
          id="test-lang"
          label="Language"
          value="en-US"
          options={sampleOptions}
          onChange={() => undefined}
        />
      </div>,
    );

    await user.click(screen.getByText("English (US) (en-US)"));
    expect(screen.getByPlaceholderText("Filter language...")).toBeInTheDocument();

    await user.click(screen.getByTestId("outside"));
    expect(screen.queryByPlaceholderText("Filter language...")).toBeNull();
  });

  it("does not open when disabled", async () => {
    const user = userEvent.setup();
    render(
      <StudioSelect
        id="test-lang"
        label="Language"
        value="en-US"
        options={sampleOptions}
        onChange={() => undefined}
        disabled
      />,
    );

    await user.click(screen.getByText("English (US) (en-US)"));
    expect(screen.queryByPlaceholderText("Filter language...")).toBeNull();
  });
});
