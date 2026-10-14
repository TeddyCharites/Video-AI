// Khai báo các composition. Kích thước, fps, độ dài đều lấy từ props
// (do Python tính trong pipeline/remotion_render.py) qua calculateMetadata.
import { Composition } from 'remotion';
import { WhiteboardVideo } from './WhiteboardVideo.jsx';
import { ShortVideo } from './ShortVideo.jsx';

// Props mẫu để mở thử bằng "npm run studio" (không dùng khi Python render)
const sampleProps = {
  width: 1280,
  height: 720,
  fps: 30,
  durationInFrames: 90,
  background: '#F6F1E3',
  title: { text: 'Tiêu đề video', subtitle: 'Video Studio Local', from: 0, durationInFrames: 90 },
  scenes: [],
  subtitles: [{ from: 20, durationInFrames: 60, text: 'Phụ đề mẫu tiếng Việt có dấu' }],
  subtitleStyle: { fontSize: 36, fontFamily: 'Arial', bottom: 36 },
  titleFont: 'Segoe Print',
  music: null,
};

const sampleShortProps = {
  ...sampleProps,
  width: 720,
  height: 1280,
  durationInFrames: 150,
  title: null,
  subtitles: [{ from: 0, durationInFrames: 60, text: 'Phụ đề lớn cho Short' }],
  subtitleStyle: { fontSize: 50, fontFamily: 'Arial', bottom: 256 },
  titleText: 'Tiêu đề Short gây tò mò',
  titleStyle: { fontSize: 52, font: 'Segoe Print' },
  clipAspect: 16 / 9,
  outro: { from: 60, durationInFrames: 90, fadeInFrames: 12, audio: null,
           text: 'Muốn biết đầy đủ? Xem video trên kênh nhé!', parentTitle: 'Video đầy đủ' },
};

const metadata = ({ props }) => ({
  durationInFrames: props.durationInFrames,
  fps: props.fps,
  width: props.width,
  height: props.height,
});

export const RemotionRoot = () => (
  <>
    <Composition id="WhiteboardVideo" component={WhiteboardVideo} defaultProps={sampleProps} calculateMetadata={metadata} />
    <Composition id="ShortVideo" component={ShortVideo} defaultProps={sampleShortProps} calculateMetadata={metadata} />
  </>
);
