import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export type StudioSelectOption = {
  value: string;
  label: string;
  displayLabel?: string;
  badge?: string;
  description?: string;
};

type StudioSelectProps = {
  id: string;
  label: string;
  value: string;
  options: readonly StudioSelectOption[];
  onChange: (nextValue: string) => void;
  disabled?: boolean;
  icon?: ReactNode;
  placeholder?: string;
};

export function StudioSelect({
  id,
  label,
  value,
  options,
  onChange,
  disabled = false,
  icon,
  placeholder = "Select an option",
}: StudioSelectProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const containerRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  const selectedOption = useMemo(
    () => options.find((opt) => opt.value === value) ?? options[0],
    [options, value],
  );

  const filteredOptions = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) {
      return options;
    }
    return options.filter(
      (opt) =>
        opt.label.toLowerCase().includes(q) ||
        (opt.displayLabel && opt.displayLabel.toLowerCase().includes(q)) ||
        opt.value.toLowerCase().includes(q) ||
        (opt.badge && opt.badge.toLowerCase().includes(q)) ||
        (opt.description && opt.description.toLowerCase().includes(q)),
    );
  }, [options, search]);

  useEffect(() => {
    if (disabled && open) {
      setOpen(false);
    }
  }, [disabled, open]);

  useEffect(() => {
    if (!open) {
      setSearch("");
      return;
    }
    function handleOutsideClick(event: MouseEvent) {
      if (
        containerRef.current &&
        event.target instanceof Node &&
        !containerRef.current.contains(event.target)
      ) {
        setOpen(false);
      }
    }
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleOutsideClick);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleOutsideClick);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open, options.length]);

  function handleToggle() {
    if (disabled || options.length === 0) {
      return;
    }
    setOpen((prev) => !prev);
  }

  function handleSelectOption(nextValue: string) {
    onChange(nextValue);
    setOpen(false);
  }

  return (
    <div
      ref={containerRef}
      className={`studio-select-field ${open ? "studio-select-open" : ""} ${
        disabled ? "studio-select-disabled" : ""
      }`}
    >
      <label htmlFor={id} className="studio-select-label">
        {label}
      </label>

      <div className="studio-select-control">
        {/* Native semantic select preserved for screen readers, keyboard tabbing, and test assertions */}
        <select
          id={id}
          className="sr-native-select"
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>

        {/* Visual shadcn/ui-inspired custom trigger */}
        <div
          className={`studio-select-trigger ${open ? "studio-select-trigger-active" : ""}`}
          onClick={handleToggle}
          aria-hidden="true"
        >
          <div className="studio-select-trigger-left">
            {icon ? <span className="studio-select-icon-box">{icon}</span> : null}
            <div className="studio-select-value-stack">
              <span className="studio-select-primary-text">
                {selectedOption
                  ? (selectedOption.displayLabel ?? selectedOption.label)
                  : placeholder}
              </span>
              {selectedOption?.description ? (
                <span className="studio-select-secondary-text">
                  {selectedOption.description}
                </span>
              ) : null}
            </div>
          </div>

          <div className="studio-select-trigger-right">
            {selectedOption?.badge ? (
              <span className="studio-select-badge">{selectedOption.badge}</span>
            ) : null}
            <span className="studio-select-chevron">
              <svg
                viewBox="0 0 20 20"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="m5.5 7.5 4.5 4.5 4.5-4.5" />
              </svg>
            </span>
          </div>
        </div>

        {/* Floating shadcn/ui-inspired Popover Menu */}
        {open ? (
          <div className="studio-select-popover" role="region" aria-label={`${label} selector`}>
            {options.length > 5 ? (
              <div className="studio-select-search-box">
                <svg
                  viewBox="0 0 20 20"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.75"
                  className="studio-select-search-icon"
                >
                  <circle cx="9" cy="9" r="5.5" />
                  <path strokeLinecap="round" d="m13.2 13.2 3.3 3.3" />
                </svg>
                <input
                  ref={searchInputRef}
                  type="text"
                  className="studio-select-search-input"
                  placeholder={`Filter ${label.toLowerCase()}...`}
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  onClick={(event) => event.stopPropagation()}
                />
              </div>
            ) : (
              <div className="studio-select-popover-header">
                <span>{label}</span>
                <span>{options.length} available</span>
              </div>
            )}

            <div className="studio-select-viewport" role="listbox" aria-label={`${label} options`}>
              {filteredOptions.length === 0 ? (
                <div className="studio-select-empty">No matching options</div>
              ) : (
                filteredOptions.map((opt) => {
                  const isSelected = opt.value === value;
                  return (
                    <div
                      key={opt.value}
                      role="option"
                      aria-selected={isSelected}
                      className={`studio-select-item ${
                        isSelected ? "studio-select-item-selected" : ""
                      }`}
                      onClick={(event) => {
                        event.stopPropagation();
                        handleSelectOption(opt.value);
                      }}
                    >
                      <div className="studio-select-item-main">
                        <span className="studio-select-item-check">
                          {isSelected ? (
                            <svg viewBox="0 0 20 20" fill="currentColor">
                              <path
                                fillRule="evenodd"
                                d="M16.704 5.29a1 1 0 0 1 .006 1.414l-7.5 7.56a1 1 0 0 1-1.42-.004l-3.5-3.56a1 1 0 1 1 1.42-1.4l2.79 2.838 6.79-6.842a1 1 0 0 1 1.414-.006Z"
                                clipRule="evenodd"
                              />
                            </svg>
                          ) : null}
                        </span>
                        <div className="studio-select-item-text">
                          <span className="studio-select-item-label">
                            {opt.displayLabel ?? opt.label}
                          </span>
                          {opt.description ? (
                            <span className="studio-select-item-desc">{opt.description}</span>
                          ) : null}
                        </div>
                      </div>
                      {opt.badge ? (
                        <span className="studio-select-item-badge">{opt.badge}</span>
                      ) : null}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
