export interface Option<T extends string> {
  value: T;
  label: string;
  title?: string;
}

export function Segmented<T extends string>({ label, options, value, onChange, testId }: {
  label: string;
  options: Option<T>[];
  value: T;
  onChange: (value: T) => void;
  testId?: string;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label} data-testid={testId}>
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={o.value === value} title={o.title}
                className={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
