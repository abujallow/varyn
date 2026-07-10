const STARFIELD_STARS = Array.from({ length: 400 }, (_, index) => {
  const pairIndex = Math.floor(index / 2);
  const isLeft = index % 2 === 0;
  const left = isLeft
    ? 4 + ((pairIndex * 23) % 43)
    : 54 + ((pairIndex * 19) % 43);
  const top = 5 + ((pairIndex * (isLeft ? 31 : 37)) % 90);

  return {
    id: `star-${index + 1}`,
    style: {
      animationDelay: `-${(index * 0.06).toFixed(2)}s`,
      left: `${left}%`,
      top: `${top}%`,
    },
  };
});

export default function OrbitalField() {
  return (
    <div className="orbital-field" aria-hidden="true">
      {STARFIELD_STARS.map((star) => (
        <span className="star" key={star.id} style={star.style} />
      ))}
      <span className="sweep-line sweep-a" />
      <span className="sweep-line sweep-b" />
    </div>
  );
}
