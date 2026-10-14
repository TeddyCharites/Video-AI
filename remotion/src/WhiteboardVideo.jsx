// Video hoàn chỉnh: [màn hình tiêu đề] → các cảnh vẽ bảng (chuyển cảnh mờ dần) + giọng đọc + phụ đề.
//
// Mọi mốc thời gian (frame bắt đầu, độ dài, chồng lấn khi chuyển cảnh, phụ đề) đã được Python tính sẵn
// và truyền qua props → file .srt và video luôn khớp nhau. Ở đây chỉ việc vẽ.
//
// File media đọc bằng staticFile(): Python chạy render với --public-dir = thư mục scenes/ của dự án.
import {
  AbsoluteFill, Audio, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame,
  useVideoConfig, Easing,
} from 'remotion';

export const WhiteboardVideo = ({ background, title, scenes, subtitles, subtitleStyle, titleFont, music }) => (
  <AbsoluteFill style={{ backgroundColor: background }}>
    {music ? <BackgroundMusic {...music} /> : null}

    {title ? (
      <Sequence from={title.from} durationInFrames={title.durationInFrames} name="Tiêu đề">
        <TitleCard {...title} font={titleFont} />
      </Sequence>
    ) : null}

    {/* Cảnh sau nằm TRÊN cảnh trước; trong đoạn chồng lấn nó mờ dần hiện ra → crossfade */}
    {scenes.map(s => (
      <Sequence key={s.id} from={s.from} durationInFrames={s.durationInFrames} name={`Cảnh ${s.id}`}>
        <Scene {...s} />
      </Sequence>
    ))}

    {subtitles.map((c, i) => (
      <Sequence key={i} from={c.from} durationInFrames={c.durationInFrames} name="Phụ đề" layout="none">
        <Subtitle text={c.text} secondary={c.secondary} style={subtitleStyle} />
      </Sequence>
    ))}
  </AbsoluteFill>
);

// Nhạc nền: lặp lại cho đủ dài video; âm lượng tính theo TỪNG khung hình:
//   - mờ dần vào ở đầu, mờ dần ra ở cuối video
//   - "ducking": quanh các đoạn có giọng đọc (speech, Python truyền sang) nhạc nhỏ xuống duckLevel,
//     chuyển mượt trong rampFrames khung hình thay vì tụt đột ngột
export const BackgroundMusic = ({ src, volume, duck, duckLevel, rampFrames, fadeInFrames, fadeOutFrames, speech }) => {
  const { durationInFrames } = useVideoConfig();
  const volumeAt = f => {
    let v = volume;
    if (fadeInFrames > 0) v *= Math.min(1, f / fadeInFrames);
    if (fadeOutFrames > 0) v *= Math.min(1, Math.max(0, durationInFrames - f) / fadeOutFrames);
    if (duck) {
      // Khoảng cách (khung hình) tới đoạn có giọng gần nhất; 0 = đang có giọng
      let dist = Infinity;
      for (const [a, b] of speech) {
        dist = Math.min(dist, f < a ? a - f : f > b ? f - b : 0);
        if (dist === 0) break;
      }
      const amount = Math.max(0, 1 - dist / rampFrames);  // 1 = giảm hết mức, 0 = không giảm
      v *= 1 - amount * (1 - duckLevel);
    }
    return v;
  };
  // loopVolumeCurveBehavior="extend": số khung hình truyền vào volumeAt KHÔNG quay về 0 mỗi vòng lặp nhạc
  // (mặc định "repeat" quay về 0 → fade-out cuối video và mốc giọng đọc bị lệch khi bài nhạc ngắn hơn video)
  return <Audio src={staticFile(src)} loop loopVolumeCurveBehavior="extend" volume={volumeAt} />;
};

const Scene = ({ clip, audio, fadeInFrames }) => {
  const frame = useCurrentFrame();
  const opacity = fadeInFrames > 0
    ? interpolate(frame, [0, fadeInFrames], [0, 1], { extrapolateRight: 'clamp' })
    : 1;
  return (
    <AbsoluteFill style={{ opacity }}>
      {/* OffthreadVideo: Remotion tự tách đúng khung hình bằng ffmpeg → chính xác từng frame */}
      <OffthreadVideo src={staticFile(clip)} muted style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
      <Audio src={staticFile(audio)} />
    </AbsoluteFill>
  );
};

// Tiêu đề "viết tay": chữ hiện dần từ trái sang phải như đang được viết, gạch chân vẽ theo sau,
// cuối cùng mờ dần sang cảnh đầu tiên.
const TitleCard = ({ text, subtitle, durationInFrames, font }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const writeEnd = Math.min(durationInFrames * 0.55, fps * 2.2);
  const reveal = interpolate(frame, [fps * 0.2, writeEnd], [0, 100], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.quad),
  });
  const underline = interpolate(frame, [writeEnd - fps * 0.3, writeEnd + fps * 0.5], [1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const subOpacity = interpolate(frame, [writeEnd, writeEnd + fps * 0.4], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const portrait = height > width;
  const fontSize = Math.round(Math.min(width, height) * (portrait ? 0.085 : 0.095));
  // Gạch chân dài theo tiêu đề (ước lượng ~0.55 × cỡ chữ mỗi ký tự), không quá 60%/45% khung hình
  const lineW = Math.round(Math.min(width * (portrait ? 0.6 : 0.45),
                                    Math.max(fontSize * 2, text.length * fontSize * 0.55 * 0.8)));
  const lineH = Math.round(fontSize * 0.5);
  return (
    <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', padding: '0 8%' }}>
      <div style={{
        fontFamily: `"${font}", "Segoe UI", sans-serif`, fontSize, color: '#2b2b2b',
        textAlign: 'center', lineHeight: 1.25, fontWeight: 700,
        clipPath: `inset(-20% ${100 - reveal}% -20% 0)`,  // phần bên phải chưa "viết" bị che
      }}>
        {text}
      </div>
      {/* Gạch chân "vẽ tay": pathLength=1 + dasharray=1 → dashoffset 1→0 là nét dài dần ra.
          Toạ độ tính bằng pixel thật (không co giãn SVG) để dasharray tính đúng theo chiều dài nét. */}
      <svg width={lineW} height={lineH} style={{ marginTop: fontSize * 0.15, overflow: 'visible' }}>
        <path d={`M ${lineW * 0.02} ${lineH * 0.6} Q ${lineW * 0.3} ${lineH * 0.2}, ${lineW * 0.55} ${lineH * 0.5} T ${lineW * 0.98} ${lineH * 0.4}`}
              fill="none" stroke="#e8890c" strokeWidth={Math.max(3, fontSize * 0.08)} strokeLinecap="round"
              pathLength={1} strokeDasharray={1} strokeDashoffset={underline} />
      </svg>
      {subtitle ? (
        <div style={{
          marginTop: fontSize * 0.35, opacity: subOpacity, fontFamily: '"Segoe UI", sans-serif',
          fontSize: Math.round(fontSize * 0.38), color: '#6b6b6b',
        }}>
          {subtitle}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

// Phụ đề: dòng chính (ngôn ngữ đang đọc) + dòng phụ tuỳ chọn (song ngữ: nhỏ hơn, màu vàng nhạt)
export const Subtitle = ({ text, secondary, style }) => {
  const line = (content, size, color) => (
    <div style={{ fontSize: size, lineHeight: 1.45 }}>
      {/* span inline (không phải phần tử flex) để hộp nền ôm sát TỪNG dòng khi chữ xuống dòng */}
      <span style={{
        color, backgroundColor: 'rgba(0, 0, 0, 0.68)',
        padding: `${size * 0.12}px ${size * 0.4}px`, borderRadius: size * 0.2,
        boxDecorationBreak: 'clone', WebkitBoxDecorationBreak: 'clone',
      }}>
        {content}
      </span>
    </div>
  );
  return (
    <AbsoluteFill style={{ justifyContent: 'flex-end', alignItems: 'center', paddingBottom: style.bottom }}>
      <div style={{
        maxWidth: '84%', textAlign: 'center', display: 'flex', flexDirection: 'column', gap: style.fontSize * 0.2,
        fontFamily: `"${style.fontFamily}", "Segoe UI", sans-serif`,
      }}>
        {line(text, style.fontSize, '#fff')}
        {secondary ? line(secondary, Math.round(style.fontSize * 0.78), '#f5e6b5') : null}
      </div>
    </AbsoluteFill>
  );
};
