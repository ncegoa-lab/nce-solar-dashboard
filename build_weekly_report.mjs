import fs from 'node:fs/promises';
import path from 'node:path';
import { SpreadsheetFile, Workbook } from '@oai/artifact-tool';

const workspace = '/Users/sushil/Documents/GOODWE';
const sourcePath = path.join(workspace, 'sems_station_data.json');
const outputDir = path.join(workspace, 'outputs', 'goodwe_weekly_report');
const outputPath = path.join(outputDir, 'goodwe_weekly_report.xlsx');

const raw = JSON.parse(await fs.readFile(sourcePath, 'utf8'));
const generatedAt = new Date(raw.generated_at);
const weekStart = new Date(generatedAt);
const day = weekStart.getDay();
const diffToMonday = day === 0 ? -6 : 1 - day;
weekStart.setDate(weekStart.getDate() + diffToMonday);
const weekEnd = new Date(weekStart);
weekEnd.setDate(weekStart.getDate() + 6);

const dateFmt = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
const weekLabel = `${dateFmt.format(weekStart)} - ${dateFmt.format(weekEnd)}`;
const generatedLabel = dateFmt.format(generatedAt);

const stations = raw.stations.map((s) => ({
  stationId: s.powerstation_id ?? '',
  stationName: s.stationname ?? '',
  status: s.status ?? '',
  capacity: Number(s.capacity ?? 0),
  currentPower: Number(s.pac_kw ?? s.pac ?? 0),
  todayGeneration: Number(s.eday ?? 0),
  monthGeneration: Number(s.emonth ?? 0),
  totalGeneration: Number(s.etotal ?? 0),
  location: s.location ?? '',
  organization: s.org_name ?? '',
}));

const sorted = [...stations]
  .map((s, i) => ({ ...s, sourceRow: i + 2 }))
  .sort((a, b) => b.todayGeneration - a.todayGeneration);

const workbook = Workbook.create();
const report = workbook.worksheets.add('Weekly Report');
const data = workbook.worksheets.add('Station Data');
const chartData = workbook.worksheets.add('Chart Source');

for (const sheet of [report, data, chartData]) {
  sheet.showGridLines = false;
}

const dataHeaders = [
  'Station ID', 'Station Name', 'Status', 'Capacity (kW)', 'Current Power (kW)',
  'Today Generation (kWh)', 'Month Generation (kWh)', 'Total Generation (kWh)',
  'Location', 'Organization'
];
data.getRange('A1:J1').values = [dataHeaders];
data.getRangeByIndexes(1, 0, stations.length, dataHeaders.length).values = stations.map((s) => [
  s.stationId, s.stationName, s.status, s.capacity, s.currentPower, s.todayGeneration,
  s.monthGeneration, s.totalGeneration, s.location, s.organization,
]);
data.getRange('A1:J1').format.fill = { color: '#17324D' };
data.getRange('A1:J1').format.font = { color: '#FFFFFF', bold: true };
data.getRange(`A1:J${stations.length + 1}`).format.borders = { preset: 'all', style: 'thin', color: '#D7DEE8' };
data.getRange(`D2:H${stations.length + 1}`).setNumberFormat('#,##0.0');
data.getRange(`A:J`).format.autofitColumns();
data.freezePanes.freezeRows(1);

report.getRange('A1:H1').merge();
report.getRange('A1').values = [['GoodWe SEMS Weekly Report']];
report.getRange('A1').format.font = { bold: true, size: 18, color: '#17324D' };
report.getRange('A2:H2').merge();
report.getRange('A2').values = [[`Week: ${weekLabel} | Data snapshot: ${generatedLabel}`]];
report.getRange('A2').format.font = { color: '#576575', size: 11 };

report.getRange('A4:B7').values = [
  ['Stations', null],
  ['Total Capacity (kW)', null],
  ['Generation Today (kWh)', null],
  ['Total Generation (kWh)', null],
];
report.getRange('B4').formulas = [[`=COUNTA('Station Data'!B2:B${stations.length + 1})`]];
report.getRange('B5').formulas = [[`=SUM('Station Data'!D2:D${stations.length + 1})`]];
report.getRange('B6').formulas = [[`=SUM('Station Data'!F2:F${stations.length + 1})`]];
report.getRange('B7').formulas = [[`=SUM('Station Data'!H2:H${stations.length + 1})`]];
report.getRange('A4:B7').format.fill = { color: '#EEF5F1' };
report.getRange('A4:A7').format.font = { bold: true, color: '#17324D' };
report.getRange('B4:B7').format.font = { bold: true, color: '#0E5A44' };
report.getRange('A4:B7').format.borders = { preset: 'outside', style: 'medium', color: '#8AB5A0' };
report.getRange('B5:B7').setNumberFormat('#,##0.0');

report.getRange('D4:E7').values = [
  ['Best Station Today', null],
  ['Best Generation (kWh)', null],
  ['Avg kWh / kW Today', null],
  ['Zero Generation Stations', null],
];
report.getRange('E4').formulas = [[`='Chart Source'!A2`]];
report.getRange('E5').formulas = [[`='Chart Source'!B2`]];
report.getRange('E6').formulas = [[`=IFERROR(B6/B5,0)`]];
report.getRange('E7').formulas = [[`=COUNTIF('Station Data'!F2:F${stations.length + 1},0)`]];
report.getRange('D4:E7').format.fill = { color: '#F3F0E8' };
report.getRange('D4:D7').format.font = { bold: true, color: '#17324D' };
report.getRange('E4:E7').format.font = { bold: true, color: '#6A4A00' };
report.getRange('D4:E7').format.borders = { preset: 'outside', style: 'medium', color: '#C4AE79' };
report.getRange('E5:E6').setNumberFormat('#,##0.0');

report.getRange('A10:H10').merge();
report.getRange('A10').values = [['Station Generation Ranking']];
report.getRange('A10').format.font = { bold: true, size: 13, color: '#17324D' };
report.getRange('A11:D11').values = [['Rank', 'Station Name', 'Today kWh', 'Total kWh']];
report.getRange('A11:D11').format.fill = { color: '#17324D' };
report.getRange('A11:D11').format.font = { bold: true, color: '#FFFFFF' };
report.getRangeByIndexes(11, 0, sorted.length, 4).values = sorted.map((s, i) => [
  i + 1, s.stationName, s.todayGeneration, s.totalGeneration,
]);
report.getRange(`A11:D${11 + sorted.length}`).format.borders = { preset: 'all', style: 'thin', color: '#D7DEE8' };
report.getRange(`C12:D${11 + sorted.length}`).setNumberFormat('#,##0.0');
report.getRange('A:D').format.autofitColumns();
report.getRange('B:B').format.columnWidth = 28;

chartData.getRange('A1:B1').values = [['Station', 'Today Generation (kWh)']];
chartData.getRange('A1:B1').format.fill = { color: '#17324D' };
chartData.getRange('A1:B1').format.font = { bold: true, color: '#FFFFFF' };
chartData.getRangeByIndexes(1, 0, sorted.length, 2).formulas = sorted.map((s) => [
  `='Station Data'!B${s.sourceRow}`,
  `='Station Data'!F${s.sourceRow}`,
]);
chartData.getRange(`B2:B${sorted.length + 1}`).setNumberFormat('#,##0.0');
chartData.getRange('A:B').format.autofitColumns();

const chart = report.charts.add('bar', chartData.getRange(`A1:B${sorted.length + 1}`));
chart.title = 'Today Generation by Station (kWh)';
chart.titleTextStyle.fontSize = 12;
chart.hasLegend = false;
chart.xAxis = { axisType: 'textAxis', textStyle: { fontSize: 9 } };
chart.yAxis = { numberFormatCode: '#,##0.0' };
chart.setPosition('F4', 'N22');

report.getRange('A24:H24').merge();
report.getRange('A24').values = [['Notes']];
report.getRange('A24').format.font = { bold: true, color: '#17324D' };
report.getRange('A25:H27').merge();
report.getRange('A25').values = [[
  'This weekly report uses the live SEMS station snapshot available from the API. The graph ranks stations by the current Today Generation value returned by SEMS at report time.'
]];
report.getRange('A25').format.wrapText = true;
report.getRange('A25').format.fill = { color: '#F7F9FB' };
report.getRange('A25').format.borders = { preset: 'outside', style: 'thin', color: '#D7DEE8' };

report.getRange('A:H').format.autofitColumns();
report.getRange('A:A').format.columnWidth = 20;
report.getRange('B:B').format.columnWidth = 24;
report.getRange('D:D').format.columnWidth = 22;
report.getRange('E:E').format.columnWidth = 22;
report.freezePanes.freezeRows(3);

chartData.visibility = 'hidden';

const summary = await workbook.inspect({
  kind: 'table',
  sheetId: 'Weekly Report',
  range: 'A1:E14',
  include: 'values,formulas',
  tableMaxRows: 20,
  tableMaxCols: 8,
});
console.log(summary.ndjson);
const errors = await workbook.inspect({
  kind: 'match',
  searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A',
  options: { useRegex: true, maxResults: 300 },
  summary: 'final formula error scan',
});
console.log(errors.ndjson);

await fs.mkdir(outputDir, { recursive: true });
const preview = await workbook.render({ sheetName: 'Weekly Report', autoCrop: 'all', scale: 1, format: 'png' });
await fs.writeFile(path.join(outputDir, 'weekly_report_preview.png'), new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);
console.log(outputPath);
