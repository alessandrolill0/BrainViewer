import './styles.css';
import '@fontsource/ibm-plex-mono/300.css';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/500.css';
import '@fontsource/ibm-plex-mono/600.css';
import '@fontsource/ibm-plex-sans-condensed/600.css';
import '@fontsource/ibm-plex-sans-condensed/700.css';

import Boundary from './components/Boundary';
import Library from './components/Library';
import Player from './components/Player';
import Report from './components/Report';
import Stage from './components/Stage';
import StatusBar from './components/StatusBar';
import { useTelemetry } from './hooks/useTelemetry';

export default function App() {
  const telemetry = useTelemetry();

  return (
    <div className="shell">
      <div className="scanlines" />
      <StatusBar telemetry={telemetry} />
      {/* One boundary per column: if one fails the others stay up. The
          transport especially must survive any fault, or the music could no
          longer be stopped. */}
      <div className="body">
        <Boundary name="LIBRERIA">
          <Library telemetry={telemetry} />
        </Boundary>
        <Boundary name="STAGE">
          <Stage telemetry={telemetry} />
        </Boundary>
        <Boundary name="REFERTO">
          <Report telemetry={telemetry} />
        </Boundary>
      </div>
      <Boundary name="TRASPORTO">
        <Player telemetry={telemetry} />
      </Boundary>
    </div>
  );
}
