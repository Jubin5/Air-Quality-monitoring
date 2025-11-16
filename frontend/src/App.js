import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';
import 'leaflet.heat'; // this attaches heatLayer to L

// Fix default Leaflet icon issue
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: require('leaflet/dist/images/marker-icon-2x.png'),
  iconUrl: require('leaflet/dist/images/marker-icon.png'),
  shadowUrl: require('leaflet/dist/images/marker-shadow.png'),
});

// Backend base URL (use env var when deployed)
const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || '';

const getAQICategory = (aqi) => {
  if (aqi <= 50) return { label: 'Good', color: '#00e400' };
  if (aqi <= 100) return { label: 'Moderate', color: '#ffff00' };
  if (aqi <= 150) return { label: 'Unhealthy for Sensitive', color: '#ff7e00' };
  if (aqi <= 200) return { label: 'Unhealthy', color: '#ff0000' };
  if (aqi <= 300) return { label: 'Very Unhealthy', color: '#8f3f97' };
  return { label: 'Hazardous', color: '#7e0023' };
};

// Heatmap layer component using leaflet.heat
function HeatmapLayer({ points, options }) {
  // points: array of [lat, lng, intensity]
  const map = useMap();

  useEffect(() => {
    if (!map) return;
    // create heat layer
    const heatLayer = L.heatLayer(points, options || { radius: 25, blur: 15, maxZoom: 12 });
    heatLayer.addTo(map);

    return () => {
      if (map && heatLayer) {
        map.removeLayer(heatLayer);
      }
    };
  }, [map, JSON.stringify(points), JSON.stringify(options)]);

  return null;
}

// AnalyticsScreen (simplified)
const AnalyticsScreen = ({ stations, historicalData }) => {
  const avgAQI = stations.length > 0
    ? Math.round(stations.reduce((sum, s) => sum + s.aqi, 0) / stations.length)
    : 0;
  return (
    <div className="p-4">
      <h2>📈 Analytics</h2>
      <p>Number of stations: {stations.length}</p>
      <p>Average AQI: {avgAQI}</p>
      {historicalData.length > 0 && (
        <div>
          <h3>Recent Trend</h3>
          {historicalData.slice(-7).map((record, idx) => (
            <div key={idx}>
              {new Date(record.timestamp).toLocaleString()}: {record.avgAqi} AQI
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

function App() {
  const [stations, setStations] = useState([]);
  const [activeTab, setActiveTab] = useState('map');
  const [historicalData, setHistoricalData] = useState([]);
  const delhiPosition = [28.6139, 77.2090];

  const fetchData = useCallback(async () => {
    try {
      const url = `${API_BASE_URL}/api/live-data`;
      const response = await axios.get(url);
      // ensure numeric AQI
      const validStations = response.data.live_data
        .filter(station => station.latitude != null && station.longitude != null && Number.isFinite(station.aqi))
        .map(s => ({ ...s, aqi: Number(s.aqi) }));
      setStations(validStations);

      if (validStations.length > 0) {
        const avgAqi = Math.round(validStations.reduce((sum, s) => sum + s.aqi, 0) / validStations.length);
        setHistoricalData(prev => [...prev, { timestamp: Date.now(), avgAqi }].slice(-30));
      }
    } catch (error) {
      console.error('Error fetching data:', error);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 60000); // 1 minute
    return () => clearInterval(interval);
  }, [fetchData]);

  // prepare heatmap points: [lat, lon, intensity]
  // normalize intensity by dividing by 500 (AQI max approx; adjust as necessary)
  const heatPoints = stations.map(s => [s.latitude, s.longitude, Math.max(0.05, s.aqi / 500)]);

  // Alerts Screen
  const AlertsScreen = () => {
    const hazardous = stations.filter(s => s.aqi > 300);
    const veryUnhealthy = stations.filter(s => s.aqi > 200 && s.aqi <= 300);
    const unhealthy = stations.filter(s => s.aqi > 150 && s.aqi <= 200);

    return (
      <div className="p-4">
        <h2>🔔 Alerts</h2>
        {hazardous.length > 0 && (
          <div>
            <h3>🚨 Hazardous (AQI > 300)</h3>
            {hazardous.map(s => (
              <div key={s.uid}>{s.station_name}: {s.aqi}</div>
            ))}
          </div>
        )}
        {veryUnhealthy.length > 0 && (
          <div>
            <h3>⚠️ Very Unhealthy (AQI 200-300)</h3>
            {veryUnhealthy.map(s => (
              <div key={s.uid}>{s.station_name}: {s.aqi}</div>
            ))}
          </div>
        )}
        {unhealthy.length > 0 && (
          <div>
            <h3>⚡ Unhealthy (AQI 150-200)</h3>
            {unhealthy.map(s => (
              <div key={s.uid}>{s.station_name}: {s.aqi}</div>
            ))}
          </div>
        )}
        {hazardous.length === 0 && veryUnhealthy.length === 0 && unhealthy.length === 0 && (
          <div>✅ No Active Alerts</div>
        )}
      </div>
    );
  };

  return (
    <div className="h-screen flex flex-col">
      <header className="bg-green-600 text-white p-4 flex justify-between">
        <span>📍 Air Quality - Delhi NCR</span>
        <div className="flex gap-2">
          <button onClick={() => setActiveTab('map')}>Map</button>
          <button onClick={() => setActiveTab('alerts')}>Alerts</button>
          <button onClick={() => setActiveTab('analytics')}>Analytics</button>
        </div>
      </header>

      <div className="flex-1 overflow-auto">
        {activeTab === 'map' && (
          <MapContainer center={delhiPosition} zoom={10} style={{ height: '90vh', width: '100%' }}>
            <TileLayer
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='&copy; OpenStreetMap contributors'
            />
            {/* Heatmap layer */}
            <HeatmapLayer points={heatPoints} options={{ radius: 25, blur: 15, maxZoom: 12 }} />

            {/* Station markers */}
            {stations.map(s => (
              <CircleMarker
                key={s.uid}
                center={[s.latitude, s.longitude]}
                radius={10}
                fillColor={getAQICategory(s.aqi).color}
                color="#fff"
                weight={2}
                fillOpacity={0.9}
              >
                <Popup>
                  <b>{s.station_name}</b><br />
                  AQI: {s.aqi}<br />
                  Last Updated: {s.last_updated}
                </Popup>
              </CircleMarker>
            ))}
          </MapContainer>
        )}
        {activeTab === 'alerts' && <AlertsScreen />}
        {activeTab === 'analytics' && (
          <AnalyticsScreen stations={stations} historicalData={historicalData} />
        )}
      </div>
    </div>
  );
}

export default App;
