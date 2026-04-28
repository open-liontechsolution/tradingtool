export default function EmptyState({ icon, title, description, action, style }) {
  return (
    <div className="empty-state" role="status" style={style}>
      {icon && <div className="empty-state-icon" aria-hidden="true">{icon}</div>}
      {title && <div className="empty-state-title">{title}</div>}
      {description && <div className="empty-state-text">{description}</div>}
      {action}
    </div>
  )
}
