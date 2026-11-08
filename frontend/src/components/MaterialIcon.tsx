type MaterialIconName = 'refresh' | 'auto_awesome' | 'tune' | 'download' | 'file_upload'

const paths: Record<MaterialIconName, string> = {
  refresh: 'M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z',
  auto_awesome: 'M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zm0 6l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5z',
  tune: 'M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v2h-8zm-4-8v2h12v-2H9zm8-6v2h4V7h-4z',
  download: 'M5 20h14v-2H5v2zM19 9h-4V3H9v6H5l7 7 7-7z',
  file_upload: 'M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z',
}

export default function MaterialIcon({ name, label }: { name: MaterialIconName; label?: string }) {
  return <svg className="material-icon" viewBox="0 0 24 24" aria-hidden={label ? undefined : true} role={label ? 'img' : undefined} aria-label={label}><path d={paths[name]} /></svg>
}
