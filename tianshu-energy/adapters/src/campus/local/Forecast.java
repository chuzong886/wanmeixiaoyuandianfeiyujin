package campus.local;

import org.json.*;
import java.math.*;
import java.time.*;
import java.util.*;

/** Conservative local estimator. Intentionally identified separately from the server's v3 engine. */
public final class Forecast {
    public static final ZoneId ZONE=ZoneId.of("Asia/Shanghai");
    public static double now(){return System.currentTimeMillis()/1000.0;}
    public static LocalDate date(double t){return Instant.ofEpochMilli((long)(t*1000)).atZone(ZONE).toLocalDate();}
    public static double at(LocalDate d,String time){return d.atTime(LocalTime.parse(time)).atZone(ZONE).toEpochSecond();}
    public static JSONObject defaults() throws Exception {return new JSONObject("{\"window_start\":\"08:00\",\"window_end\":\"22:00\",\"max_queries\":8,\"min_gap_minutes\":60,\"warning_kwh\":20,\"critical_kwh\":3,\"clear_kwh\":25,\"reserve_kwh\":3,\"warning_hours\":24,\"critical_hours\":6,\"forecast_notice_enabled\":false,\"forecast_notice_hours\":12,\"phone_cooldown_hours\":6,\"max_emergency_requests\":2}");}
    public static JSONObject options(JSONObject m) throws Exception {JSONObject o=defaults(),raw=m.optJSONObject("options");if(raw!=null){Iterator<String> k=raw.keys();while(k.hasNext()){String s=k.next();o.put(s,raw.get(s));}}return o;}
    public static void validate(JSONObject o) throws Exception {
        LocalTime start=LocalTime.parse(o.getString("window_start")),end=LocalTime.parse(o.getString("window_end"));if(!end.isAfter(start))throw new CampusClient.Failure("查询窗口结束时间必须晚于开始时间");
        for(String k:new String[]{"max_queries","min_gap_minutes","warning_kwh","critical_kwh","clear_kwh","reserve_kwh","forecast_notice_hours","phone_cooldown_hours","max_emergency_requests"})if(!Double.isFinite(o.getDouble(k))||o.getDouble(k)<0)throw new CampusClient.Failure("监控参数无效："+k);
        if(o.getInt("max_queries")<1||o.getInt("max_queries")>24||o.getDouble("min_gap_minutes")<60||o.getDouble("min_gap_minutes")>1440||o.getDouble("critical_kwh")>o.getDouble("warning_kwh")||o.getDouble("clear_kwh")<o.getDouble("warning_kwh"))throw new CampusClient.Failure("查询额度、最短间隔或预警线设置无效");
    }
    public static JSONArray intervals(JSONArray history) throws Exception {
        JSONArray out=new JSONArray();JSONObject prev=null;
        for(int i=0;i<history.length();i++){
            JSONObject r=history.getJSONObject(i);
            if(prev!=null){double hours=(r.getDouble("ts")-prev.getDouble("ts"))/3600;BigDecimal delta=new BigDecimal(prev.getString("balance_raw")).subtract(new BigDecimal(r.getString("balance_raw")));double energy=delta.doubleValue();
                if(hours>0)out.put(new JSONObject().put("start",prev.getDouble("ts")).put("ts",r.getDouble("ts")).put("hours",hours).put("energy",Math.max(0,energy)).put("usable",hours>=1&&hours<=48&&energy>0?1:0).put("tag",energy<0?"recharge_possible":energy==0?"unchanged":hours<1?"short_interval":hours>48?"gap_possible":"usable"));
            }
            // A cached plateau does not prove zero consumption. Keep its original
            // anchor until a real balance change arrives, rather than concentrating
            // the entire debit into the final short interval.
            if(prev==null||Double.compare(prev.getDouble("balance"),r.getDouble("balance"))!=0)prev=r;
        }return out;
    }
    public static JSONObject analyse(JSONArray history,JSONObject o,double now) throws Exception {
        JSONObject a=new JSONObject().put("algorithm_version","Tianshu Local 1.0 (conservative)").put("samples",0).put("rate",0).put("warning",false).put("critical",false).put("provisional",true).put("quality","first");
        if(history.length()==0)return a;
        JSONObject last=history.getJSONObject(history.length()-1);double ts=last.getDouble("ts"),balance=last.getDouble("balance");
        JSONArray all=intervals(history);double segment=0;for(int i=0;i<all.length();i++)if(all.getJSONObject(i).getString("tag").equals("recharge_possible"))segment=all.getJSONObject(i).getDouble("ts");
        double[] rates=new double[3];int samples=0;double coverage=0;
        for(int h=0;h<3;h++){double life=new double[]{6,24,48}[h],energy=0,hours=0;
            for(int i=0;i<all.length();i++){JSONObject x=all.getJSONObject(i);if(x.getInt("usable")==0||x.getDouble("start")<segment||ts-x.getDouble("ts")>14*86400)continue;double weight=Math.pow(.5,(ts-x.getDouble("ts"))/3600/life);energy+=x.getDouble("energy")*weight;hours+=x.getDouble("hours")*weight;if(h==0){samples++;coverage+=x.getDouble("hours");}}
            rates[h]=hours>0?energy/hours:0;
        }
        double rate=(rates[0]+rates[1]+rates[2])/3,guard=Math.max(rates[0],Math.max(rates[1],rates[2]))*1.25,age=Math.max(0,(now-ts)/3600);boolean available=samples>=3&&coverage>=12&&rate>1e-6&&age<72;
        double estimated=Math.max(0,balance-rate*age),safeHours=guard>1e-6?Math.max(0,(balance-o.getDouble("reserve_kwh"))/guard-age):Double.POSITIVE_INFINITY;
        a.put("rate",rate).put("samples",samples).put("provisional",!available).put("quality",last.optString("quality","first")).put("balance",balance).put("warning",balance<=o.getDouble("warning_kwh")||(available&&safeHours<=o.optDouble("warning_hours",24))).put("critical",balance<=o.getDouble("critical_kwh")||(available&&safeHours<=o.optDouble("critical_hours",6))).put("estimated_now_kwh",estimated).put("coverage_hours",coverage).put("guard_rate",guard).put("available",available).put("data_age_hours",age);
        return a;
    }
    public static JSONObject forecast(JSONObject a,JSONObject reading,JSONObject o,double now) throws Exception {
        JSONObject f=new JSONObject().put("available",a.optBoolean("available"));if(reading==null)return f;
        double balance=reading.getDouble("balance"),origin=reading.getDouble("ts"),rate=a.getDouble("rate"),guard=a.getDouble("guard_rate"),age=a.getDouble("data_age_hours"),left=guard>0?Math.max(0,(balance-o.getDouble("reserve_kwh"))/guard-age):0;
        JSONArray points=new JSONArray();for(int i=0;i<=12;i++)points.put(new JSONObject().put("h",i*6).put("mean",i*6*rate).put("upper",i*6*guard));
        f.put("origin",origin).put("points",points).put("estimated_now_kwh",a.getDouble("estimated_now_kwh")).put("data_age_hours",age).put("data_state",age>48?"lost":age>24?"stale":"fresh").put("remaining_hours",left).put("horizon_left_hours",Math.max(0,72-age)).put("beyond_horizon",left>Math.max(0,72-age)).put("empty_at",left<=72&&rate>0?now+left*3600:JSONObject.NULL).put("action_by",a.optBoolean("available")&&guard>0?origin+Math.max(0,(balance-o.getDouble("reserve_kwh"))/guard-2)*3600:JSONObject.NULL);return f;
    }
    public static JSONArray daily(JSONArray intervals,int days,double now) throws Exception {
        JSONArray out=new JSONArray();for(int i=days-1;i>=0;i--){LocalDate d=date(now).minusDays(i);double start=at(d,"00:00"),end=start+86400,energy=0,hours=0;for(int j=0;j<intervals.length();j++){JSONObject x=intervals.getJSONObject(j);if(x.getInt("usable")==0)continue;double overlap=Math.max(0,Math.min(end,x.getDouble("ts"))-Math.max(start,x.getDouble("start")));energy+=x.getDouble("energy")*overlap/(x.getDouble("hours")*3600);hours+=overlap/3600;}out.put(new JSONObject().put("date",d.toString()).put("energy",hours>0?energy:JSONObject.NULL).put("covered_hours",hours));}return out;
    }
    public static JSONObject plan(JSONObject m,JSONArray attempts,JSONObject a,double now) throws Exception {
        JSONObject o=options(m);LocalDate day=date(now);double start=at(day,o.getString("window_start")),end=at(day,o.getString("window_end")),last=0;int count=0;
        for(int i=0;i<attempts.length();i++){JSONObject r=attempts.getJSONObject(i);if(r.getInt("meter_id")!=m.getInt("id")||r.getString("kind").equals("manual"))continue;last=Math.max(last,r.getDouble("ts"));if(date(r.getDouble("ts")).equals(day))count++;}
        double minGap=o.getDouble("min_gap_minutes")*60,gap=Math.max(minGap,(end-start)/Math.max(1,o.getInt("max_queries")-1));if(a.optBoolean("critical")||a.optBoolean("warning"))gap=minGap;
        double due=Math.max(start,last>0?last+gap:start);due=Math.max(due,m.optDouble("retry_after",0));String status=now<start?"outside_window":"feasible";
        if(count>=o.getInt("max_queries")){due=at(day.plusDays(1),o.getString("window_start"));status="budget_exhausted";}
        else if(now>end||due>end){due=at(day.plusDays(1),o.getString("window_start"));status="outside_window";}
        return new JSONObject().put("day",day.toString()).put("next",m.optBoolean("enabled",true)?due:JSONObject.NULL).put("status",status).put("count",count).put("target",o.getInt("max_queries"));
    }
}
