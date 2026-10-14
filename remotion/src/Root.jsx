// Khai báo composition "WhiteboardVideo". Kích thước, fps, độ dài đều lấy từ props
// (do Python tính trong pipeline/remotion_render.py) qua calculateMetadata.
import { Composition } from 'remotion';
import { WhiteboardVideo } from './WhiteboardVideo.jsx';

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

export const RemotionRoot = () => (
  <Composition
    id="WhiteboardVideo"
    component={WhiteboardVideo}
    defaultProps={sampleProps}
    calculateMetadata={({ props }) => ({
      durationInFrames: props.durationInFrames,
      fps: props.fps,
      width: props.width,
      height: props.height,
    })}
  />
);
