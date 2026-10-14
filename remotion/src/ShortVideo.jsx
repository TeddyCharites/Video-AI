// Short dọc (9:16): tiêu đề cố định phía trên, clip vẽ bảng (thường là 16:9 của video dài) đặt giữa,
// phụ đề LỚN bên dưới, cuối cùng là màn hình kết đọc câu dẫn về video đầy đủ.
//
// Mọi mốc thời gian do Python tính (pipeline/remotion_render.build_short_timeline) — ở đây chỉ vẽ.
import {
  AbsoluteFill, Audio, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame,
  useVideoConfig, Easing,
} from 'remotion';
import { BackgroundMusic, Subtitle } from './WhiteboardVideo.jsx';

export const ShortVideo = ({
  background, scenes, subtitles, subtitleStyle, music, titleText, titleStyle, clipAspect, outro,
}) => {
  const { width, height } = useVideoConfig();
  // Clip ngang (16:9): khung clip rộng hết bề ngang, đặt dưới vùng tiêu đề. Clip dọc: phủ kín khung.
  const landscape = clipAspect >= 1;
  // Clip ngang hiện như một "tấm thẻ" (lề, bo góc, bóng nhẹ): màu giấy trong clip lệch nhẹ sau khi
  // mã hoá video → để thành khung có chủ ý thay vì một dải màu lệch có mép lộ.
  const margin = Math.round(width * 0.04);
  const box = landscape
    ? { top: Math.round(height * 0.2), left: margin, right: margin,
        height: Math.round((width - 2 * margin) / clipAspect), card: true }
    : { top: 0, left: 0, right: 0, height, card: false };
  // Phụ đề nằm trong khoảng trống dưới clip (neo đáy cách mép dưới 20% chiều cao)
  const subStyle = { ...subtitleStyle, bottom: Math.round(height * 0.2) };
  return (
    <AbsoluteFill style={{ backgroundColor: background }}>
      {music ? <BackgroundMusic {...music} /> : null}

      {scenes.map(s => (
        <Sequence key={s.id} from={s.from} durationInFrames={s.durationInFrames} name={`Cảnh ${s.id}`}>
          <ShortScene {...s} box={box} />
        </Sequence>
      ))}

      <TitleBar text={titleText} style={titleStyle} overClip={!landscape} />

      {subtitles.map((c, i) => (
        <Sequence key={i} from={c.from} durationInFrames={c.durationInFrames} name="Phụ đề" layout="none">
          <Subtitle text={c.text} secondary={c.secondary} style={subStyle} />
        </Sequence>
      ))}

      <Sequence from={outro.from} durationInFrames={outro.durationInFrames} name="Kết">
        <Outro {...outro} background={background} font={titleStyle.font} />
      </Sequence>
    </AbsoluteFill>
  );
};

const ShortScene = ({ clip, audio, fadeInFrames, box }) => {
  const frame = useCurrentFrame();
  const opacity = fadeInFrames > 0 ? interpolate(frame, [0, fadeInFrames], [0, 1], { extrapolateRight: 'clamp' }) : 1;
  return (
    <AbsoluteFill style={{ opacity }}>
      <div style={{
        position: 'absolute', left: box.left, right: box.right, top: box.top, height: box.height, overflow: 'hidden',
        ...(box.card ? { borderRadius: 18, boxShadow: '0 6px 24px rgba(0, 0, 0, 0.16)', border: '2px solid rgba(0, 0, 0, 0.06)' } : {}),
      }}>
        <OffthreadVideo src={staticFile(clip)} muted style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      </div>
      <Audio src={staticFile(audio)} />
    </AbsoluteFill>
  );
};

// Tiêu đề Short: chữ viết tay, tối đa 2 dòng, hiện suốt video (người xem lướt tới giữa chừng vẫn biết đang xem gì)
const TitleBar = ({ text, style, overClip }) => (
  <AbsoluteFill style={{ justifyContent: 'flex-start', alignItems: 'center', paddingTop: '5%' }}>
    <div style={{
      maxWidth: '88%', textAlign: 'center', fontFamily: `"${style.font}", "Segoe UI", sans-serif`,
      fontSize: style.fontSize, fontWeight: 700, lineHeight: 1.2, color: '#2b2b2b',
      display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
      ...(overClip ? { background: 'rgba(246, 241, 227, 0.85)', padding: '0.2em 0.5em', borderRadius: 12 } : {}),
    }}>
      {text}
    </div>
  </AbsoluteFill>
);

// Màn hình kết: câu dẫn (có giọng đọc) + tên video đầy đủ với gạch chân vẽ tay
const Outro = ({ audio, text, parentTitle, fadeInFrames, background, font }) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();
  const opacity = fadeInFrames > 0 ? interpolate(frame, [0, fadeInFrames], [0, 1], { extrapolateRight: 'clamp' }) : 1;
  const draw = interpolate(frame, [fps * 0.4, fps * 1.4], [1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.quad),
  });
  const big = Math.round(width * 0.075);
  const lineW = Math.round(width * 0.6), lineH = Math.round(big * 0.5);
  return (
    <AbsoluteFill style={{ backgroundColor: background, opacity, alignItems: 'center', justifyContent: 'center',
                           padding: '0 9%', textAlign: 'center' }}>
      {audio ? <Audio src={staticFile(audio)} /> : null}
      <div style={{ fontFamily: '"Segoe UI", sans-serif', fontSize: Math.round(big * 0.62), color: '#555', lineHeight: 1.35 }}>
        {text}
      </div>
      <div style={{ marginTop: big * 0.6, fontFamily: `"${font}", "Segoe UI", sans-serif`, fontSize: big,
                    fontWeight: 700, color: '#2b2b2b', lineHeight: 1.2 }}>
        {parentTitle}
      </div>
      <svg width={lineW} height={lineH} style={{ marginTop: big * 0.15, overflow: 'visible' }}>
        <path d={`M ${lineW * 0.02} ${lineH * 0.6} Q ${lineW * 0.3} ${lineH * 0.2}, ${lineW * 0.55} ${lineH * 0.5} T ${lineW * 0.98} ${lineH * 0.4}`}
              fill="none" stroke="#e8890c" strokeWidth={Math.max(4, big * 0.09)} strokeLinecap="round"
              pathLength={1} strokeDasharray={1} strokeDashoffset={draw} />
      </svg>
    </AbsoluteFill>
  );
};
