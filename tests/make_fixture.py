"""Write a tiny fake GTFS feed (Orlando-ish) for testing build.py offline."""
import io, sys, zipfile
from datetime import date, timedelta

def make(path, kind="bus"):
    today = date.today()
    start = (today - timedelta(30)).strftime("%Y%m%d")
    end = (today + timedelta(60)).strftime("%Y%m%d")
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\nA,Test,https://example.com,America/New_York\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type,route_color\n"
                      "R8,A,8,W OAK RIDGE RD/INTL DR,3,0B5CAD\nR60,A,60,LYMMO ORANGE LINE,3,F57C00\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                        f"WK,1,1,1,1,1,0,0,{start},{end}\nSA,0,0,0,0,0,1,0,{start},{end}\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
                     "S1,N ORANGE AVE & E CENTRAL BLVD,28.5427,-81.3790\nS2,LYNX CENTRAL STATION BAY A,28.5490,-81.3810\n"
                     "S3,W OAK RIDGE RD & S ORANGE BLOSSOM TRL,28.4710,-81.3970\nS4,INTL DR & SAND LAKE RD,28.4500,-81.4710\n",
        "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
                      + "".join(f"SH8,{28.471-0.0002*i},{-81.397-0.0037*i},{i}\n" for i in range(21))
                      + "SH60,28.5427,-81.3790,1\nSH60,28.5460,-81.3800,2\nSH60,28.5490,-81.3810,3\n",
    }
    trips = ["route_id,service_id,trip_id,direction_id,shape_id"]
    st = ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"]
    n = 0
    for svc, first, last, step in (("WK", 4*60+30, 26*60+47-60, 15), ("SA", 4*60+50, 25*60+17-60, 25)):
        t = first
        while t <= last:
            n += 1
            tid = f"T8_{n}"
            trips.append(f"R8,{svc},{tid},0,SH8")
            st.append(f"{tid},{t//60:02d}:{t%60:02d}:00,{t//60:02d}:{t%60:02d}:00,S3,1")
            e = t + 60
            st.append(f"{tid},{e//60:02d}:{e%60:02d}:00,{e//60:02d}:{e%60:02d}:00,S4,2")
            t += step
    for k in range(40):
        t = 6*60 + 5*k
        tid = f"T60_{k}"
        trips.append(f"R60,WK,{tid},0,SH60")
        st.append(f"{tid},{t//60:02d}:{t%60:02d}:00,{t//60:02d}:{t%60:02d}:00,S1,1")
        st.append(f"{tid},{t//60:02d}:{t%60+0:02d}:00,{t//60:02d}:{t%60:02d}:00,S2,2")
    files["trips.txt"] = "\n".join(trips) + "\n"
    files["stop_times.txt"] = "\n".join(st) + "\n"
    files["feed_info.txt"] = f"feed_publisher_name,feed_publisher_url,feed_lang,feed_end_date\nTest,https://example.com,en,{end}\n"
    with zipfile.ZipFile(path, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)

if __name__ == "__main__":
    make(sys.argv[1])
