using System;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.Win32;
using Windows.Graphics;
namespace Tianshu.Energy;
public partial class App:Application {
 static bool Simulation => Path.GetFileNameWithoutExtension(Environment.ProcessPath??"").Contains("Simulator",StringComparison.OrdinalIgnoreCase);
 static string AppKey => Simulation?"TianshuSimulator":"TianshuEnergy";
 Window? window;Process? backend;System.Windows.Forms.NotifyIcon? tray;Mutex? instance;bool exiting=false,refreshing=false;string url="",data="";
 readonly HttpClient client=new HttpClient(new HttpClientHandler{UseProxy=false}){Timeout=TimeSpan.FromSeconds(6)};
 readonly TextBlock state=new TextBlock{Text=Simulation?"正在启动模拟平台…":"正在启动能源采集服务…",FontSize=23,TextWrapping=TextWrapping.Wrap};
 readonly TextBlock detail=new TextBlock{TextWrapping=TextWrapping.Wrap};
 readonly TextBox logs=new TextBox{IsReadOnly=true,AcceptsReturn=true,TextWrapping=TextWrapping.Wrap,MinHeight=210};
 readonly DispatcherTimer timer=new DispatcherTimer{Interval=TimeSpan.FromSeconds(3)};
 public App(){InitializeComponent();}
 protected override async void OnLaunched(LaunchActivatedEventArgs args){
  instance=new Mutex(true,Simulation?"Local\\TianshuSimulatorStandalone":"Local\\TianshuEnergyStandalone",out bool created);if(!created){Exit();return;}
  window=new Window{Title=Simulation?"天枢 · 模拟平台服务中心":"天枢能源 · 本地服务中心"};window.AppWindow.Resize(new SizeInt32(820,630));
  var panel=new StackPanel{Padding=new Thickness(30),Spacing=17};panel.Children.Add(new TextBlock{Text=Simulation?"天枢模拟平台 / SIMULATOR":"天枢能源 / TIANSHU ENERGY",FontSize=25,FontWeight=Microsoft.UI.Text.FontWeights.SemiBold});panel.Children.Add(state);panel.Children.Add(detail);
  var buttons=new StackPanel{Orientation=Orientation.Horizontal,Spacing=10};var open=new Button{Content=Simulation?"打开模拟平台":"打开能源大屏"};var folder=new Button{Content="打开数据目录"};var quit=new Button{Content="退出服务"};
  open.Click+=(_,_)=>OpenDashboard();folder.Click+=(_,_)=>{if(Directory.Exists(data))Process.Start(new ProcessStartInfo(data){UseShellExecute=true});};quit.Click+=(_,_)=>Shutdown();buttons.Children.Add(open);buttons.Children.Add(folder);buttons.Children.Add(quit);panel.Children.Add(buttons);
  var startup=new ToggleSwitch{Header="登录 Windows 后自动启动",IsOn=AutoStart()};startup.Toggled+=(_,_)=>SetAutoStart(startup.IsOn);panel.Children.Add(startup);panel.Children.Add(new TextBlock{Text="服务日志",FontSize=16});panel.Children.Add(logs);panel.Children.Add(new TextBlock{Text=Simulation?"本程序仅模拟电表 API 和设备网关，不连接物理硬件。\n默认不连接天枢；请使用专用测试网关及独立设备映射。":"网页大屏每秒更新，后台持续采集。关闭窗口收至托盘；退出服务后停止采集。\n首次运行为模拟空间，不连接任何真实设备。",TextWrapping=TextWrapping.Wrap});window.Content=new ScrollViewer{Content=panel};if(Array.IndexOf(Environment.GetCommandLineArgs(),"--no-window")<0)window.Activate();
  tray=new System.Windows.Forms.NotifyIcon{Icon=System.Drawing.SystemIcons.Information,Text=Simulation?"天枢 · 模拟平台":"天枢能源 · 本地采集",Visible=true};tray.DoubleClick+=(_,_)=>window.DispatcherQueue.TryEnqueue(()=>{window.AppWindow.Show();window.Activate();});var menu=new System.Windows.Forms.ContextMenuStrip();menu.Items.Add(Simulation?"打开模拟平台":"打开能源大屏",null,(_,_)=>window.DispatcherQueue.TryEnqueue(OpenDashboard));menu.Items.Add("显示服务窗口",null,(_,_)=>window.DispatcherQueue.TryEnqueue(()=>{window.AppWindow.Show();window.Activate();}));menu.Items.Add("退出服务",null,(_,_)=>window.DispatcherQueue.TryEnqueue(Shutdown));tray.ContextMenuStrip=menu;window.AppWindow.Closing+=(_,e)=>{if(!exiting){e.Cancel=true;window.AppWindow.Hide();}};
  try{await Start();await Refresh();timer.Tick+=async(_,_)=>await Refresh();timer.Start();if(Array.IndexOf(Environment.GetCommandLineArgs(),"--no-browser")<0)OpenDashboard();}catch(Exception e){state.Text="启动失败";detail.Text=e.Message;}
 }
 void OpenDashboard(){if(url!="")Process.Start(new ProcessStartInfo(url){UseShellExecute=true});}
 void Log(string text){logs.Text=DateTimeOffset.UtcNow.ToOffset(TimeSpan.FromHours(8)).ToString("HH:mm:ss")+"  "+text+Environment.NewLine+logs.Text;if(logs.Text.Length>16000)logs.Text=logs.Text.Substring(0,16000);}
 async Task Start(){data=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),AppKey);string[] args=Environment.GetCommandLineArgs();int i=Array.IndexOf(args,"--data-dir");if(i>=0&&i+1<args.Length)data=Path.GetFullPath(args[i+1]);Directory.CreateDirectory(data);string servicePort=Simulation?"8792":"0";int portIndex=Array.IndexOf(args,"--port");if(Simulation&&portIndex>=0){if(portIndex+1>=args.Length||!int.TryParse(args[portIndex+1],out int specifiedPort)||specifiedPort<0||specifiedPort>65535)throw new IOException("模拟端口应为0至65535");servicePort=specifiedPort.ToString();}string root=AppContext.BaseDirectory;var info=new ProcessStartInfo(Path.Combine(root,"runtime","python.exe")){UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,WorkingDirectory=root};foreach(var s in new[]{"-u",Path.Combine(root,"backend",Simulation?"simulator.py":"server.py"),"--parent-pipe","--port",servicePort,"--data-dir",data,"--web-dir",Path.Combine(root,"web")})info.ArgumentList.Add(s);backend=Process.Start(info)??throw new IOException("无法启动采集内核");_=backend.StandardError.ReadToEndAsync();string? line=await backend.StandardOutput.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(30));if(line==null)throw new IOException("服务已退出，请检查运行文件是否齐全");using(var doc=JsonDocument.Parse(line))url=doc.RootElement.GetProperty("url").GetString()!;Log("本地服务启动成功；使用独立数据库");Log("网页入口："+url);Log(Simulation?"模拟来源：软件生成，无物理设备；默认不连接网关":"采集来源：模拟、网关和已配置的第三方 API 设备");}
 async Task Refresh(){if(refreshing)return;refreshing=true;try{using var doc=JsonDocument.Parse(await client.GetStringAsync(url+"/api/health"));var ts=doc.RootElement.GetProperty("heartbeat").GetDouble();bool failed=doc.RootElement.TryGetProperty("last_error",out var error)&&error.ValueKind!=JsonValueKind.Null;bool online=!failed&&DateTimeOffset.UtcNow.ToUnixTimeSeconds()-ts<5;state.Text=online?(Simulation?"● 模拟平台运行中 · 每秒更新":"● 采集服务运行中 · 每秒更新"):"采集心跳已过期";detail.Text="最近心跳："+(ts>0?DateTimeOffset.FromUnixTimeSeconds((long)ts).ToOffset(TimeSpan.FromHours(8)).ToString("yyyy-MM-dd HH:mm:ss"):"等待首次采集")+"（北京时间）\n网页入口："+url+"\n数据目录："+data;tray!.Text=Simulation?(online?"天枢模拟平台 · 运行中":"天枢模拟平台 · 心跳待检查"):(online?"天枢能源 · 采集运行中":"天枢能源 · 心跳待检查");}catch{state.Text="本地服务连接中断";detail.Text="请确认文件完整，退出后重新启动。数据目录："+data;}finally{refreshing=false;}}
 bool AutoStart(){using var key=Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run");return key?.GetValue(AppKey)!=null;}
 void SetAutoStart(bool on){try{using var key=Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run");if(on)key.SetValue(AppKey,"\""+Environment.ProcessPath+"\"");else key.DeleteValue(AppKey,false);Log(on?"已启用登录自启动":"已关闭登录自启动");}catch{Log("保存自启动设置失败");}}
 void Shutdown(){exiting=true;timer.Stop();tray?.Dispose();try{if(backend!=null&&!backend.HasExited){backend.StandardInput.Close();if(!backend.WaitForExit(5000))backend.Kill(true);}}catch{}client.Dispose();window?.Close();instance?.Dispose();Exit();}
}
