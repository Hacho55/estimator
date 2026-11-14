export function euros(value: number): string {
  return `${value.toLocaleString("es-ES", { maximumFractionDigits: 0 })} €`;
}

export function range(min: number, max: number, unit: string): string {
  return min === max ? `${min.toLocaleString("es-ES")} ${unit}` : `${min.toLocaleString("es-ES")}–${max.toLocaleString("es-ES")} ${unit}`;
}
